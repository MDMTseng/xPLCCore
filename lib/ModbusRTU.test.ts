import { describe, it, expect } from 'vitest';
import { splitLines } from './ModbusRTU';

describe('splitLines', () => {
  it('keeps a message split over two chunks', () => {
    const a = splitLines('', '{"a":1}\n{"b":');
    expect(a).toEqual({ lines: ['{"a":1}'], rest: '{"b":' });
    const b = splitLines(a.rest, '2}\n');
    expect(b).toEqual({ lines: ['{"b":2}'], rest: '' });
  });
  it('drops blank lines and handles several messages per chunk', () => {
    expect(splitLines('', '{"a":1}\n\n{"b":2}\n')).toEqual({ lines: ['{"a":1}', '{"b":2}'], rest: '' });
  });
});
