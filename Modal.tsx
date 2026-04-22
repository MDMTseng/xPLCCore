import React, { useState, useEffect, useRef } from 'react';

interface ModalProps {
  isOpen: boolean;
  onClose: () => void;
  children: React.ReactNode;
  closeClickCount?: number;
  closeClickTimeout?: number;
  buttonSize?: number;
  iconSize?: number;
  circleRadius?: number;
  strokeWidth?: number;
  style?: React.CSSProperties;
}

const modalOverlayStyle: React.CSSProperties = {
  position: 'fixed',
  top: 0,
  left: 0,
  right: 0,
  bottom: 0,
  backgroundColor: 'rgba(0, 0, 0, 0.5)',
  display: 'flex',
  justifyContent: 'center',
  alignItems: 'center',
  zIndex: 1000,
};

const modalContentStyle: React.CSSProperties = {
  background: 'white',
  padding: '20px',
  borderRadius: '8px',
  width: '800px',
  height: '800px',
  position: 'relative',
};

const closeButtonStyle = (iconSize: number): React.CSSProperties => ({
  background: 'none',
  border: 'none',
  fontSize: `${iconSize}px`,
  cursor: 'pointer',
  padding: 0,
  zIndex: 1,
  lineHeight: 1,
});

const closeButtonContainerStyle = (buttonSize: number): React.CSSProperties => ({
  position: 'absolute',
  top: '10px',
  right: '10px',
  width: `${buttonSize}px`,
  height: `${buttonSize}px`,
  display: 'flex',
  justifyContent: 'center',
  alignItems: 'center',
});

const svgStyle: React.CSSProperties = {
  position: 'absolute',
  top: 0,
  left: 0,
  width: '100%',
  height: '100%',
  transform: 'rotate(-90deg)',
  zIndex: 0,
};

export const Modal: React.FC<ModalProps> = ({ 
  isOpen, 
  onClose, 
  children, 
  closeClickCount = 3, 
  closeClickTimeout = 200,
  buttonSize = 44,
  iconSize = 24,
  circleRadius = 20,
  strokeWidth = 4,
  style = {},
}) => {
  const [clicks, setClicks] = useState(0);
  const [animationKey, setAnimationKey] = useState(0);
  const timerRef = useRef<NodeJS.Timeout>();

  const handleCloseAttempt = () => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
    }

    const newClicks = clicks + 1;

    if (newClicks >= closeClickCount) {
      onClose();
      setClicks(0);
    } else {
      setClicks(newClicks);
      setAnimationKey(prev => prev + 1);
      timerRef.current = setTimeout(() => {
        setClicks(0);
      }, closeClickTimeout);
    }
  };

  useEffect(() => {
    if (!isOpen) {
      setClicks(0);
      if (timerRef.current) {
        clearTimeout(timerRef.current);
      }
    }
  }, [isOpen]);

  if (!isOpen) {
    return null;
  }

  const circumference = 2 * Math.PI * circleRadius;
  const clickProgress = (clicks / closeClickCount);
  const strokeDashoffset = circumference * (1 - clickProgress);

  const countdownAnimation = `
    @keyframes countdown {
      from { stroke-dashoffset: 0; }
      to { stroke-dashoffset: ${circumference}; }
    }
  `;

  const combinedModalContentStyle = {
    ...modalContentStyle,
    ...style,
  };
  
  return (
    <div style={modalOverlayStyle} onClick={()=>{}}>
      <style>{countdownAnimation}</style>
      <div style={combinedModalContentStyle} onClick={(e) => e.stopPropagation()}>
        <div style={closeButtonContainerStyle(buttonSize)}>
          <svg style={svgStyle} viewBox={`0 0 ${buttonSize} ${buttonSize}`}>
            <circle
              cx={buttonSize / 2}
              cy={buttonSize / 2}
              r={circleRadius}
              fill="transparent"
              stroke="#eee"
              strokeWidth={strokeWidth}
            />
            <circle
              cx={buttonSize / 2}
              cy={buttonSize / 2}
              r={circleRadius}
              fill="transparent"
              stroke="#007bff"
              strokeWidth={strokeWidth}
              strokeDasharray={circumference}
              strokeDashoffset={strokeDashoffset}
              style={{ transition: 'stroke-dashoffset 0.3s ease' }}
            />
            {clicks > 0 && (
              <circle
                key={animationKey}
                cx={buttonSize / 2}
                cy={buttonSize / 2}
                r={circleRadius}
                fill="transparent"
                stroke="rgba(0,0,0,0.2)"
                strokeWidth={strokeWidth}
                strokeDasharray={circumference}
                strokeDashoffset={circumference}
                style={{ animation: `countdown ${closeClickTimeout / 1000}s linear` }}
              />
            )}
          </svg>
          <button style={closeButtonStyle(iconSize)} onClick={handleCloseAttempt}>&times;</button>
        </div>
        {children}
      </div>
    </div>
  );
};

export default Modal;
