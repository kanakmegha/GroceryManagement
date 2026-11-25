// @types/node is not a project dependency; declare the minimal Node surface this test needs.
declare const require: (id: string) => any; // eslint-disable-line @typescript-eslint/no-explicit-any
declare const __dirname: string;
const { readdirSync, readFileSync } = require('fs') as {
  readdirSync: (p: string) => string[];
  readFileSync: (p: string, enc: string) => string;
};
const { join } = require('path') as { join: (...p: string[]) => string };

const DEMO_DIR = join(__dirname, '..');
const DEMO_ROUTES_DIR = join(__dirname, '..', '..', '..', '..', 'app', 'demo');

function sources(dir: string): { file: string; text: string }[] {
  return readdirSync(dir)
    .filter((f) => /\.(ts|tsx)$/.test(f))
    .map((f) => ({ file: f, text: readFileSync(join(dir, f), 'utf8') }));
}

const all = [...sources(DEMO_DIR), ...sources(DEMO_ROUTES_DIR)];

describe('demo/auth isolation (static guarantees)', () => {
  it('finds the demo sources', () => {
    expect(all.length).toBeGreaterThan(10);
  });

  it('demo code never imports Clerk or the authenticated client/household context', () => {
    for (const { file, text } of all) {
      expect({ file, clerk: /@clerk\//.test(text) }).toEqual({ file, clerk: false });
      expect({ file, ctx: /HouseholdContext|useApiClient|useHousehold\b/.test(text) }).toEqual({
        file,
        ctx: false,
      });
      expect({ file, authed: /createApiClient\(/.test(text) }).toEqual({ file, authed: false });
    }
  });

  it('demo code never references non-demo API paths', () => {
    const apiPaths = all.flatMap(({ file, text }) =>
      [...text.matchAll(/\/api\/v1\/[A-Za-z0-9_\-/]*/g)].map((m) => ({ file, path: m[0] }))
    );
    expect(apiPaths.length).toBeGreaterThan(0);
    for (const { file, path } of apiPaths) {
      if (file.endsWith('.test.ts')) continue; // tests intentionally reference forbidden paths
      expect({ file, ok: path.startsWith('/api/v1/demo/') }).toEqual({ file, ok: true });
    }
  });

  it('demo code issues no mutating HTTP calls', () => {
    for (const { file, text } of all) {
      if (file.endsWith('.test.ts')) continue;
      expect({ file, m: /method:\s*['"](POST|PUT|PATCH|DELETE)['"]/i.test(text) }).toEqual({
        file,
        m: false,
      });
      expect({ file, m: /useMutation|\.post\(|\.request\(/.test(text) }).toEqual({ file, m: false });
    }
  });

  it('demo query keys are demo-prefixed so caches cannot collide with authenticated data', () => {
    const provider = readFileSync(join(DEMO_DIR, 'DemoProvider.tsx'), 'utf8');
    expect(provider).toContain("queryKey: ['demo', path]");
    expect(provider).toContain('new QueryClient(');
  });
});
