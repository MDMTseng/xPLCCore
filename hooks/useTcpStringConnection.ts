import { useCallback, useEffect, useRef, useState } from 'react';

type RxHandler = (data: string) => boolean;

export const useTcpStringConnection = (
  shouldConnect: boolean,
  host: string,
  port: number,
  onReceive: RxHandler,
) => {
  const [status, setStatus] = useState(0);
  const socketRef = useRef<any | null>(null);
  const rxHandlerRef = useRef(onReceive);

  useEffect(() => {
    rxHandlerRef.current = onReceive;
  }, [onReceive]);

  useEffect(() => {
    const netFactory = (window as any).require?.('net');
    if (!netFactory) {
      console.warn('TCP connection requires Electron environment with net module available.');
      return;
    }

    if (shouldConnect) {
      if (!socketRef.current) {
        setStatus(1);
        const client = netFactory.createConnection({ host, port }, () => {
          setStatus(2);
        });

        socketRef.current = client;

        client.on('data', (data: any) => {

          let data_str=data.toString();
          // console.log("rx_data:",data_str);
          rxHandlerRef.current?.(data_str);
        });

        client.on('close', () => {
          setStatus(0);
          socketRef.current = null;
        });

        client.on('error', (error: Error) => {
          console.error('TCP connection error:', error);
          setStatus(-1);
          client.destroy();
          socketRef.current = null;
        });
      }
    } else if (socketRef.current) {
      socketRef.current.destroy();
      socketRef.current = null;
      setStatus(0);
    }

    return () => {
      if (socketRef.current) {
        socketRef.current.destroy();
        socketRef.current = null;
      }
    };
  }, [host, port, shouldConnect]);

  const send = useCallback(
    (payload: string) => {
      if (socketRef.current && !socketRef.current.destroyed) {
        socketRef.current.write(payload);
        return true;
      }
      return false;
    },
    [status],
  );

  return {
    status,
    send,
  };
};
