import { subscribe } from 'node:diagnostics_channel';
import { writeSync } from 'node:fs';

// Raw messages, URLs, headers and stacks can contain credentials. Emit only enums.
const codes = new Set([
  'UND_ERR_CONNECT_TIMEOUT', 'UND_ERR_HEADERS_TIMEOUT', 'UND_ERR_BODY_TIMEOUT',
  'UND_ERR_SOCKET', 'UND_ERR_ABORTED', 'UND_ERR_DESTROYED', 'UND_ERR_CLOSED',
  'UND_ERR_PRX_TLS', 'ECONNRESET', 'ECONNREFUSED', 'ECONNABORTED', 'ETIMEDOUT',
  'ENOTFOUND', 'EAI_AGAIN', 'EHOSTUNREACH', 'ENETUNREACH', 'EPIPE',
  'ERR_TLS_CERT_ALTNAME_INVALID', 'CERT_HAS_EXPIRED', 'CERT_NOT_YET_VALID',
  'DEPTH_ZERO_SELF_SIGNED_CERT', 'SELF_SIGNED_CERT_IN_CHAIN',
  'UNABLE_TO_VERIFY_LEAF_SIGNATURE', 'UNABLE_TO_GET_ISSUER_CERT_LOCALLY',
  'ERR_SSL_WRONG_VERSION_NUMBER', 'ERR_SSL_TLSV1_ALERT_INTERNAL_ERROR',
]);
const names = new Set([
  'Error', 'TypeError', 'AggregateError', 'ConnectTimeoutError', 'SocketError',
  'HeadersTimeoutError', 'BodyTimeoutError', 'RequestAbortedError', 'AbortError',
  'ClientDestroyedError', 'ClientClosedError', 'SecureProxyConnectionError',
]);
const syscalls = new Set(['connect', 'getaddrinfo', 'read', 'write', 'syscall']);

function field(value, key) {
  try { return value?.[key]; } catch { return undefined; }
}

subscribe('undici:request:error', (event) => {
  // Diagnostics must never change the request's failure or retry behavior.
  try {
    const origin = new URL(field(field(event, 'request'), 'origin'));
    if (origin.protocol !== 'https:' || origin.hostname !== 'api.deepseek.com') return;
    const pending = [field(event, 'error')];
    const seen = new Set();
    const causes = [];
    while (pending.length && seen.size < 8) {
      const error = pending.shift();
      if (!error || typeof error !== 'object' || seen.has(error)) continue;
      seen.add(error);
      const name = field(error, 'name');
      const code = field(error, 'code');
      const syscall = field(error, 'syscall');
      const errno = field(error, 'errno');
      causes.push({
        type: names.has(name) ? name : 'UNKNOWN',
        code: codes.has(code) ? code : 'UNKNOWN',
        ...(syscalls.has(syscall) ? { syscall } : {}),
        ...(Number.isInteger(errno) && Math.abs(errno) < 100000 ? { errno } : {}),
      });
      pending.push(field(error, 'cause'));
      const children = field(error, 'errors');
      if (Array.isArray(children)) {
        for (let i = 0; i < Math.min(children.length, 8); i++) pending.push(field(children, i));
      }
    }
    writeSync(2, '[dsh-transport] ' + JSON.stringify({
      time: new Date().toISOString(), provider: 'deepseek',
      event: 'undici:request:error', causes,
    }) + '\n');
  } catch { /* Diagnostic failures must not interrupt the agent. */ }
});
