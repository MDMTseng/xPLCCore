import React, { useCallback, useRef, useState } from 'react';
import { cmd } from './lib/protocol';

// Define the props for the RelativeMovePad component
interface RelativeMovePadProps {
  /**
   * Callback function that fires when the user drags.
   * It receives the horizontal (dx) and vertical (dy) distance
   * from the point where the interaction started.
   */
  onRelativeMove: (offset_x: number, offset_y: number, dx: number, dy: number) => void;

  onPressDown: () => void;
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

    // Release pointer capture to allow other elements to receive pointer events
    padRef.current?.releasePointerCapture(event.pointerId);

    // Clean up global listeners
    window.removeEventListener('pointermove', handlePointerMove);
    window.removeEventListener('pointerup', handlePointerUp);

    // Reset cursor style
    if (padRef.current) {
      padRef.current.style.cursor = 'grab';
    }
  }, [handlePointerMove]); // Dependency: handlePointerMove

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

export const JoggingPad: React.FC<JoggingPadProps> = ({ speedFactor_XY,speedFactor_Z,sendTcpMsgPack }) => {

      
    const _this = useRef<any>({
    }).current;

    const [current_location, setCurrentLocation] = useState<{X:number,Y:number,Z:number}|undefined>(undefined);

    //console.log("current_location",current_location);
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
            {current_location
              ? `X: ${current_location.X.toFixed(3)}, Y: ${current_location.Y.toFixed(3)}, Z: ${current_location.Z.toFixed(3)}`
              : 'undefined'}
          </div>
         <RelativeMovePad id="jog_xy" style={{background:"black", flex:"1"}}
         onPressDown={async()=>{
          _this.jog_base_location=undefined;                
          await sendTcpMsgPack(cmd.WaitForMotionStop())
          _this.jog_base_location = await sendTcpMsgPack(cmd.ReadLatestCmdLocation())
          _this.jog_wait_prev_cmd=false;

          _this.LP_Speed=0;
          _this.LP_pre_time=Date.now();
         }}
         onRelativeMove={async(offset_x: number, offset_y: number,dx:number,dy:number)=>{
          if(_this.jog_base_location===undefined || _this.jog_wait_prev_cmd==true){
            return;
          }
          //console.log(offset_x,offset_y);
          
          let dxy_dist=Math.sqrt(dx*dx+dy*dy);
          let current_time=Date.now();
          let time_diff=current_time-_this.LP_pre_time;
          _this.LP_pre_time=current_time;

          let speed=dxy_dist/time_diff*1000;//DISTANCE PER SECOND
          _this.LP_Speed=_this.LP_Speed*0.9+speed*0.1;

          let adj_alpha=0;//0~1
          let pointer_speed_cap=300;
          if(_this.LP_Speed>pointer_speed_cap){
            adj_alpha=1;
          }
          else
          {
            adj_alpha=_this.LP_Speed/pointer_speed_cap;
          }

          let multiplier=adj_alpha*speedFactor_XY;



          console.log("speed",speed,time_diff,dxy_dist,"multiplier",multiplier);

          _this.jog_base_location.X += dx*multiplier;
          _this.jog_base_location.Y += -dy*multiplier;




          _this.jog_wait_prev_cmd=true;
          await sendTcpMsgPack(cmd.G1({
            X: _this.jog_base_location.X,
            Y: _this.jog_base_location.Y,
            Z: _this.jog_base_location.Z,
          }))

          setCurrentLocation({..._this.jog_base_location});
          _this.jog_wait_prev_cmd=false;
         }}
         />

        <RelativeMovePad id="jog_z" style={{background:"red", width:"30px"}}
         onPressDown={async()=>{
          _this.jog_base_location=undefined;                
          await sendTcpMsgPack(cmd.WaitForMotionStop())
          _this.jog_base_location = await sendTcpMsgPack(cmd.ReadLatestCmdLocation())
          _this.jog_wait_prev_cmd=false;
         }}
         onRelativeMove={async(offset_x: number, offset_y: number,dx:number,dy:number)=>{
          if(_this.jog_base_location===undefined || _this.jog_wait_prev_cmd==true){
            return;
          }
          _this.jog_base_location.Z -= dy*speedFactor_Z;
          _this.jog_wait_prev_cmd=true;
          await sendTcpMsgPack(cmd.G1({
            X: _this.jog_base_location.X,
            Y: _this.jog_base_location.Y,
            Z: _this.jog_base_location.Z,
          }))
          setCurrentLocation({..._this.jog_base_location});

          _this.jog_wait_prev_cmd=false;
         }}
         />



        </div>
    );
}

export default JoggingPad;
