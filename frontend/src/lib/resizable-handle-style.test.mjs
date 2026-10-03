import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const resizableSource = await readFile(
  new URL('../components/ui/resizable.tsx', import.meta.url),
  'utf8',
);
const pageSource = await readFile(
  new URL('../app/page.tsx', import.meta.url),
  'utf8',
);
const handleTags = pageSource.match(/<ResizableHandle\b[^>]*\/>/g) ?? [];

function classesForHandle(id) {
  const handleTag = handleTags.find((tag) => tag.includes(`id="${id}"`));
  assert.ok(handleTag, `Expected ResizableHandle ${id}`);
  return (handleTag.match(/className="([^"]*)"/)?.[1] ?? '').split(/\s+/);
}

test('resizable handle base is transparent', () => {
  assert.match(resizableSource, /\bbg-transparent\b/);
  assert.doesNotMatch(
    resizableSource,
    /\bbg-border\b|\bbg-slate-200\b|\bdark:bg-slate-800\b/,
  );
});

test('resizable handle focus state does not paint a ring around the hit area', () => {
  assert.doesNotMatch(
    resizableSource,
    /\bfocus-visible:ring(?:-[^\s"]+)?\b|\bdark:focus-visible:ring(?:-[^\s"]+)?\b/,
  );
});

test('resizable handle use sites do not override the base with transparent backgrounds', () => {
  assert.equal(handleTags.length, 3);

  for (const handleTag of handleTags) {
    const className = handleTag.match(/className="([^"]*)"/)?.[1] ?? '';
    const classes = className.split(/\s+/);

    assert.equal(classes.includes('bg-transparent'), false);
    assert.equal(classes.includes('after:bg-transparent'), false);
    assert.equal(classes.includes('hover:bg-transparent'), false);
  }
});

test('vertical resizable handles keep a transparent six-pixel hit area', () => {
  for (const id of ['feed-detail-divider', 'detail-chat-divider']) {
    const classes = classesForHandle(id);
    assert.equal(classes.includes('w-1.5'), true);
    assert.equal(classes.includes('after:w-full'), true);
    assert.equal(classes.includes('px-[2.5px]'), false);
    assert.equal(classes.includes('bg-clip-content'), false);
  }
});
