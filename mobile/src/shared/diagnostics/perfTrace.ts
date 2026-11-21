const isDev = typeof __DEV__ !== 'undefined' ? __DEV__ : false;

const renderCounts: Record<string, number> = {};
const mountCounts: Record<string, number> = {};
const unmountCounts: Record<string, number> = {};
const requestCounts: Record<string, number> = {};

let seq = 0;

function nextId(): string {
  seq += 1;
  return `diag_${seq}`;
}

export function traceRender(screen: string): number {
  const count = (renderCounts[screen] ?? 0) + 1;
  renderCounts[screen] = count;
  if (isDev && (count <= 3 || count % 10 === 0)) {
    console.debug(`[Perf][Render] ${screen} #${count} @ ${new Date().toISOString()}`);
  }
  return count;
}

export function traceMount(screen: string): void {
  const count = (mountCounts[screen] ?? 0) + 1;
  mountCounts[screen] = count;
  if (isDev) {
    console.debug(`[Perf][Mount] ${screen} #${count} @ ${new Date().toISOString()}`);
  }
}

export function traceUnmount(screen: string): void {
  const count = (unmountCounts[screen] ?? 0) + 1;
  unmountCounts[screen] = count;
  if (isDev) {
    console.debug(`[Perf][Unmount] ${screen} #${count} @ ${new Date().toISOString()}`);
  }
}

export function traceApiRequest(endpoint: string, reason: string): string {
  const reqId = nextId();
  const count = (requestCounts[endpoint] ?? 0) + 1;
  requestCounts[endpoint] = count;
  if (isDev) {
    console.debug(
      `[Perf][API] ${reqId} ${endpoint} (count=${count}, reason=${reason}) @ ${new Date().toISOString()}`
    );
  }
  return reqId;
}

export function traceTransition(scope: string, detail: string): void {
  if (isDev) {
    console.debug(`[Perf][State] ${scope}: ${detail} @ ${new Date().toISOString()}`);
  }
}

export function getPerfSnapshot() {
  return {
    renders: { ...renderCounts },
    mounts: { ...mountCounts },
    unmounts: { ...unmountCounts },
    requests: { ...requestCounts },
  };
}
