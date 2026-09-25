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
  // Bumped RECONNECT_MS after the link drops while it should be up: the
  // effect below then connects again (vision restarting used to need the
  // operator to reconnect by hand).
  const [retry, setRetry] = useState(0);
  const RECONNECT_MS = 2000;

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

        let retrying = false;
        const retryLater = () => {
          if (retrying) return;
          retrying = true;
          setTimeout(() => setRetry((n) => n + 1), RECONNECT_MS);
        };
        client.on('close', () => {
          setStatus(0);
          if (socketRef.current === client) socketRef.current = null;
          retryLater();
        });

        client.on('error', (error: Error) => {
          console.error('TCP connection error:', error);
          setStatus(-1);
          client.destroy();
          if (socketRef.current === client) socketRef.current = null;
          retryLater();
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
  }, [host, port, shouldConnect, retry]);

  // Stable: it reads the socket from the ref, so users of `send` do not
  // change (and re-run their effects) on every status change.
  const send = useCallback(
    (payload: string) => {
      if (socketRef.current && !socketRef.current.destroyed) {
        socketRef.current.write(payload);
        return true;
      }
      return false;
    },
    [],
  );

  return {
    status,
    send,
  };
};
