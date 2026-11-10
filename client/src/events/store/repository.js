// EventRepository — abstract interface for the browser.
// Mirrors server/shared/events/store/repository.py.

export class EventRepository {
  async append(event) { throw new Error('abstract'); }
  async getById(id) { throw new Error('abstract'); }
  async getByCorrelationId(correlationId, limit = 1000) { throw new Error('abstract'); }
  async getByTraceId(traceId, limit = 1000) { throw new Error('abstract'); }
  async getByType(type, limit = 1000) { throw new Error('abstract'); }
  async getSince(seq, limit = 1000) { throw new Error('abstract'); }
  async latestSeq() { throw new Error('abstract'); }
  async count() { throw new Error('abstract'); }
  async clear() { throw new Error('abstract'); }
  async close() { throw new Error('abstract'); }
}
