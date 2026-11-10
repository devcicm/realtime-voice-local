// Result<T, E> — functional result type for explicit error handling.
// Mirrors server/shared/result.py. No exceptions for expected error paths.
//
//   const res = Result.fromThrowing(() => parseInt("abc", 10));
//   if (res.isErr()) return console.error(res.error);
//   res.unwrap(); // 0/NaN depending on engine
//
//   const res2 = await Result.fromPromise(fetch(url));
//   res2.map(r => r.json()).flatMap(j => Result.fromThrowing(() => j.value));

export class Ok {
  constructor(value) { this.value = value; this._ok = true; }
  isOk() { return true; }
  isErr() { return false; }
  map(f) { try { return new Ok(f(this.value)); } catch (e) { return new Err(e); } }
  flatMap(f) { return f(this.value); }
  unwrap() { return this.value; }
  unwrapOr(defaultValue) { return this.value; }
  toJSON() { return { ok: true, value: this.value }; }
  [Symbol.toPrimitive](hint) { return hint === 'string' ? `Ok(${JSON.stringify(this.value)})` : this.value; }
}

export class Err {
  constructor(error) { this.error = error; this._ok = false; }
  isOk() { return false; }
  isErr() { return true; }
  map(_) { return this; }
  flatMap(_) { return this; }
  unwrap() { throw new Error(`unwrap on Err: ${JSON.stringify(this.error)}`); }
  unwrapOr(defaultValue) { return defaultValue; }
  toJSON() { return { ok: false, error: this.error instanceof Error ? this.error.message : this.error }; }
  [Symbol.toPrimitive](hint) { return hint === 'string' ? `Err(${this.error?.message ?? this.error})` : undefined; }
}

export const Result = {
  Ok: (v) => new Ok(v),
  Err: (e) => new Err(e),
  fromThrowing: (fn) => {
    try { return Result.Ok(fn()); } catch (e) { return Result.Err(e); }
  },
  fromPromise: async (p) => {
    try { return Result.Ok(await p); } catch (e) { return Result.Err(e); }
  },
  fromThunkWith: (fn) => {  // fn returns a promise
    try { return Result.fromPromise(fn()); } catch (e) { return Result.Err(e); }
  },
  all: (results) => {
    const values = [];
    for (const r of results) {
      if (r.isErr()) return Result.Err(r.error);
      values.push(r.value);
    }
    return Result.Ok(values);
  },
};
