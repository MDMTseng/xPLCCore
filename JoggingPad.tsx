import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { JogStreamer, type XYZ } from './lib/jog';
import { JOG } from './lib/production/params';

// Define the props for the RelativeMovePad component
interface RelativeMovePadProps {
  /**
   * Callback function that fires when the user drags.
   * It receives the horizontal (dx) and vertical (dy) distance
   * from the point where the interaction started.
   */
  onRelativeMove: (offset_x: number, offset_y: number, dx: number, dy: number) => void;

  onPressDown: () => void;
  onRelease?: () => void;
  style?: React.CSSProperties;
  className?: string;
  children?: React.ReactNode;
  id?: string;
}

/**
 * A component that captures pointer drag movements over its entire area
 * and reports the relative change in position.
 */
export const RelativeMovePad: React.FC<RelativeMovePadProps> = ({
  id,
  onRelativeMove,
  onPressDown,
  onRelease,
  style,
  className,
  children,
}) => {
  // Use refs to store state without causing re-renders
  const isDraggingRef = useRef(false);
  const startPosRef = useRef({ x: 0, y: 0 });
  const prePosRef = useRef({ x: 0, y: 0 });
  const padRef = useRef<HTMLDivElement>(null);

  // Memoize move handler to keep a stable reference for event listeners
  const handlePointerMove = useCallback((event: PointerEvent) => {
    if (!isDraggingRef.current) return;

    // Calculate the difference from the start position
    const offset_x = event.clientX - startPosRef.current.x;
    const offset_y = event.clientY - startPosRef.current.y;
    const dx = event.clientX - prePosRef.current.x;
    const dy = event.clientY - prePosRef.current.y;

    prePosRef.current = { x: event.clientX, y: event.clientY };

    onRelativeMove(offset_x, offset_y, dx, dy);
  }, [onRelativeMove]); // Dependency: onRelativeMove

  // Memoize up handler for cleanup
  const handlePointerUp = useCallback((event: PointerEvent) => {
    if (!isDraggingRef.current) return;

    isDraggingRef.current = false;
    onRelease?.();

    // Release pointer capture to allow other elements to receive pointer events
    padRef.current?.releasePointerCapture(event.pointerId);

    // Clean up global listeners
    window.removeEventListener('pointermove', handlePointerMove);
    window.removeEventListener('pointerup', handlePointerUp);

    // Reset cursor style
    if (padRef.current) {
      padRef.current.style.cursor = 'grab';
    }
  }, [handlePointerMove, onRelease]);

  // Handler for the initial press
  const handlePointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    isDraggingRef.current = true;
    startPosRef.current = { x: event.clientX, y: event.clientY };
    prePosRef.current = { x: event.clientX, y: event.clientY };
    onPressDown();
    // Prevent default actions like text selection or image dragging
    event.preventDefault();

    // Capture the pointer to ensure this element receives all subsequent events
    padRef.current?.setPointerCapture(event.pointerId);

    // Add listeners to the window to track movement anywhere on the page
    window.addEventListener('pointermove', handlePointerMove);
    window.addEventListener('pointerup', handlePointerUp);

    // Update cursor to indicate an active drag
    if (padRef.current) {
      padRef.current.style.cursor = 'grabbing';
    }
  };

  // Combine default and user-provided styles
  const combinedStyles: React.CSSProperties = {
    width: '100%',
    height: '100%',
    cursor: 'grab',
    touchAction: 'none', // Essential for touch devices to prevent scrolling
    userSelect: 'none',  // Prevents text selection during drag
    ...style,
  };

  return (
    <div
      ref={padRef}
      onPointerDown={handlePointerDown}
      style={combinedStyles}
      className={className}
    >
      {children}
    </div>
  );
};

interface JoggingPadProps {
  speedFactor_XY:number,
  speedFactor_Z:number,
    sendTcpMsgPack: (data: any, await_tracking?: boolean) => Promise<any> | boolean | undefined;
}

// Drag pads for XY (black) and Z (red). The pads only turn pointer deltas
// into millimetres; lib/jog.ts streams them to the PLC as blended steps.
export const JoggingPad: React.FC<JoggingPadProps> = ({ speedFactor_XY,speedFactor_Z,sendTcpMsgPack }) => {
  const [current_location, setCurrentLocation] = useState<XYZ|undefined>(undefined);
  const [error, setError] = useState<string|undefined>(undefined);
  const lpRef = useRef({ speed: 0, t: 0 });

  const streamer = useMemo(() => new JogStreamer(
    (pkt) => {
      const r = sendTcpMsgPack(pkt);
      if (r === false || r === undefined) return Promise.reject(new Error('PLC not connected'));
      return Promise.resolve(r);
    },
    (p) => setCurrentLocation(p),
    (e: any) => setError(String(e?.message ?? e)),
  ), [sendTcpMsgPack]);
  useEffect(() => () => streamer.stop(), [streamer]);

  const press = () => {
    setError(undefined);
    lpRef.current = { speed: 0, t: Date.now() };
    void streamer.press();
  };

  return (
    <div style={{width:"100%",height:"100%", display:"flex", gap:"10px"}}>
      <div style={{
        position: 'absolute',
        top: 0,
        left: 0,
        background: 'rgba(0,0,0,0.5)',
        color: 'white',
        padding: '5px',
        zIndex: 1000
      }}>
        {error ? `jog stopped: ${error}`
          : current_location
            ? `X: ${current_location.X.toFixed(3)}, Y: ${current_location.Y.toFixed(3)}, Z: ${current_location.Z.toFixed(3)}`
            : 'undefined'}
      </div>
      <RelativeMovePad id="jog_xy" style={{background:"black", flex:"1"}}
        onPressDown={press} onRelease={() => streamer.release()}
        onRelativeMove={(_ox: number, _oy: number, dx: number, dy: number)=>{
          // Pointer acceleration: slow drags move finely, fast ones up to
          // speedFactor_XY mm per pixel (low-passed pointer speed).
          const now = Date.now();
          const lp = lpRef.current;
          const dt = Math.max(1, now - lp.t);
          lp.t = now;
          lp.speed = lp.speed * 0.9 + (Math.hypot(dx, dy) / dt * 1000) * 0.1;
          const k = Math.min(1, lp.speed / JOG.POINTER_FULL_SPEED) * speedFactor_XY;
          streamer.move({ X: dx * k, Y: -dy * k });
        }}
      />
      <RelativeMovePad id="jog_z" style={{background:"red", width:"30px"}}
        onPressDown={press} onRelease={() => streamer.release()}
        onRelativeMove={(_ox: number, _oy: number, _dx: number, dy: number)=>{
          streamer.move({ Z: -dy * speedFactor_Z });
        }}
      />
    </div>
  );
}

export default JoggingPad;
