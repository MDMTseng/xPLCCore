interface RbfInterpolatorOptions {
  rbf: (r: number) => number;
}

/**
 * Minimal matrix helpers for the calibration math.
 */
const SimpleMatrix = {
  invert(matrix: number[][]): number[][] {
    const n = matrix.length;
    if (!n || n !== matrix[0].length) {
      throw new Error('Matrix must be square.');
    }

    const identity: number[][] = Array.from({ length: n }, (_, i) =>
      Array.from({ length: n }, (_, j) => (i === j ? 1 : 0)),
    );
    const augmented = matrix.map((row, i) => [...row, ...identity[i]]);

    for (let i = 0; i < n; i++) {
      let maxRow = i;
      for (let k = i + 1; k < n; k++) {
        if (Math.abs(augmented[k][i]) > Math.abs(augmented[maxRow][i])) {
          maxRow = k;
        }
      }

      [augmented[i], augmented[maxRow]] = [augmented[maxRow], augmented[i]];

      const pivot = augmented[i][i];
      if (Math.abs(pivot) < 1e-10) {
        throw new Error('Matrix is singular and cannot be inverted.');
      }

      for (let j = i; j < 2 * n; j++) {
        augmented[i][j] /= pivot;
      }

      for (let k = 0; k < n; k++) {
        if (k === i) continue;
        const factor = augmented[k][i];
        for (let j = i; j < 2 * n; j++) {
          augmented[k][j] -= factor * augmented[i][j];
        }
      }
    }

    return augmented.map((row) => row.slice(n));
  },

  multiply(matrix: number[][], vector: number[]): number[] {
    return matrix.map((row) => row.reduce((sum, val, j) => sum + val * vector[j], 0));
  },

  multiplyMatrices(matrixA: number[][], matrixB: number[][]): number[][] {
    const aRows = matrixA.length;
    const aCols = matrixA[0].length;
    const bRows = matrixB.length;
    const bCols = matrixB[0].length;

    if (aCols !== bRows) {
      throw new Error('Matrix dimensions are not compatible for multiplication.');
    }

    const result = Array.from({ length: aRows }, () => Array(bCols).fill(0));
    for (let i = 0; i < aRows; i++) {
      for (let j = 0; j < bCols; j++) {
        for (let k = 0; k < aCols; k++) {
          result[i][j] += matrixA[i][k] * matrixB[k][j];
        }
      }
    }

    return result;
  },

  transpose(matrix: number[][]): number[][] {
    if (!matrix.length || !matrix[0].length) return [];

    const rows = matrix.length;
    const cols = matrix[0].length;
    const transposed = Array.from({ length: cols }, () => Array(rows).fill(0));

    for (let i = 0; i < rows; i++) {
      for (let j = 0; j < cols; j++) {
        transposed[j][i] = matrix[i][j];
      }
    }

    return transposed;
  },
};

class RbfInterpolator {
  private points: [number, number][] = [];
  private coeffs: number[] = [];

  constructor(private readonly options: RbfInterpolatorOptions) {}

  train(points: [number, number][], values: number[]): void {
    this.points = points;
    const n = points.length;
    const matrixSize = n + 3;

    if (n < 3) {
      this.coeffs = [];
      return;
    }

    const matrix: number[][] = Array.from({ length: matrixSize }, () => Array(matrixSize).fill(0));

    for (let i = 0; i < n; i++) {
      for (let j = 0; j < n; j++) {
        const dx = points[i][0] - points[j][0];
        const dy = points[i][1] - points[j][1];
        const dist = Math.sqrt(dx * dx + dy * dy);
        matrix[i][j] = this.options.rbf(dist);
      }

      matrix[i][n] = 1;
      matrix[i][n + 1] = points[i][0];
      matrix[i][n + 2] = points[i][1];
    }

    for (let j = 0; j < n; j++) {
      matrix[n][j] = 1;
      matrix[n + 1][j] = points[j][0];
      matrix[n + 2][j] = points[j][1];
    }

    const rhs: number[] = Array(matrixSize).fill(0);
    values.forEach((value, index) => {
      rhs[index] = value;
    });

    try {
      const inverse = SimpleMatrix.invert(matrix);
      this.coeffs = SimpleMatrix.multiply(inverse, rhs);
    } catch (error) {
      console.error('Matrix inversion failed during RBF training.', error);
      this.coeffs = [];
    }
  }

  predict(newPoint: [number, number]): number {
    if (!this.coeffs.length) return 0;

    const n = this.points.length;
    const [x, y] = newPoint;
    const trend =
      this.coeffs[n] + this.coeffs[n + 1] * x + this.coeffs[n + 2] * y;

    let rbfSum = 0;
    for (let i = 0; i < n; i++) {
      const dx = this.points[i][0] - x;
      const dy = this.points[i][1] - y;
      const dist = Math.sqrt(dx * dx + dy * dy);
      rbfSum += this.coeffs[i] * this.options.rbf(dist);
    }

    return trend + rbfSum;
  }
}

export interface CalibrationRecord {
  ObjOnCamCoord: { x: number; y: number };
  ObjOnRobotCoord: { X: number; Y: number; Z: number };
}

export interface CalibrationParameters {
  paramsX: number[];
  paramsY: number[];
  rbfErrorX: RbfInterpolator;
  rbfErrorY: RbfInterpolator;
  rbfZ: RbfInterpolator;
}

const thinPlateKernel = (r: number) => (r === 0 ? 0 : r * r * Math.log(r));

export function buildCalibrationModel(
  records: CalibrationRecord[],
): CalibrationParameters | null {
    const n = records.length;
    if (n < 3) {
      console.error('Insufficient data: At least 3 calibration points are required.');
      return null;
    }

    const cameraPoints: [number, number][] = records.map((record) => [
      record.ObjOnCamCoord.x,
      record.ObjOnCamCoord.y,
    ]);

    const robotX = records.map((record) => record.ObjOnRobotCoord.X);
    const robotY = records.map((record) => record.ObjOnRobotCoord.Y);
    const robotZ = records.map((record) => record.ObjOnRobotCoord.Z);

    const designMatrix = cameraPoints.map(([x, y]) => [x, y, 1]);

    let paramsX: number[];
    let paramsY: number[];

    try {
      const A_T = SimpleMatrix.transpose(designMatrix);
      const A_T_A = SimpleMatrix.multiplyMatrices(A_T, designMatrix);
      const pseudoInverse = SimpleMatrix.multiplyMatrices(SimpleMatrix.invert(A_T_A), A_T);

      paramsX = SimpleMatrix.multiply(pseudoInverse, robotX);
      paramsY = SimpleMatrix.multiply(pseudoInverse, robotY);
    } catch (error) {
      console.error('Failed to compute affine transformation due to matrix error.', error);
      return null;
    }

    const errorsX = records.map(
      (record) =>
        record.ObjOnRobotCoord.X -
        (paramsX[0] * record.ObjOnCamCoord.x +
          paramsX[1] * record.ObjOnCamCoord.y +
          paramsX[2]),
    );

    const errorsY = records.map(
      (record) =>
        record.ObjOnRobotCoord.Y -
        (paramsY[0] * record.ObjOnCamCoord.x +
          paramsY[1] * record.ObjOnCamCoord.y +
          paramsY[2]),
    );

    const rbfErrorX = new RbfInterpolator({ rbf: thinPlateKernel });
    rbfErrorX.train(cameraPoints, errorsX);

    const rbfErrorY = new RbfInterpolator({ rbf: thinPlateKernel });
    rbfErrorY.train(cameraPoints, errorsY);

    const rbfZ = new RbfInterpolator({ rbf: thinPlateKernel });
    rbfZ.train(cameraPoints, robotZ);

    return { paramsX, paramsY, rbfErrorX, rbfErrorY, rbfZ };
}

export function predictRobotCoordinates(
  params: CalibrationParameters,
  cameraPoint: { x: number; y: number },
): { X: number; Y: number; Z: number } {
  const { paramsX, paramsY, rbfErrorX, rbfErrorY, rbfZ } = params;
  const { x, y } = cameraPoint;

  const affineX = paramsX[0] * x + paramsX[1] * y + paramsX[2];
  const affineY = paramsY[0] * x + paramsY[1] * y + paramsY[2];

  const correctionX = rbfErrorX.predict([x, y]);
  const correctionY = rbfErrorY.predict([x, y]);
  const predictedZ = rbfZ.predict([x, y]);

  return {
    X: affineX + correctionX,
    Y: affineY + correctionY,
    Z: predictedZ,
  };
}
