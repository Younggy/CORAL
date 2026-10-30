import assert from 'node:assert/strict';
import { test } from 'node:test';
import { layoutNotes, placeLabels } from './notesGraphLayout.ts';

const nodes = Array.from({ length: 80 }, (_, i) => ({ id: String(i), title: `Original note ${i}` }));
const edges = nodes.slice(1).map((node, i) => ({ from: String(i), to: node.id, kind: 'references' }));

function checkBounds(positions, width, height) {
  for (const p of positions.values()) {
    assert.ok(Number.isFinite(p.x) && Number.isFinite(p.y));
    assert.ok(p.x >= 0 && p.x <= width && p.y >= 0 && p.y <= height);
  }
}

test('dense layouts preserve nodes, fit the viewport and do not force a label grid', () => {
  const input = structuredClone({ nodes, edges });
  const positions = layoutNotes(nodes, edges, 850, 560);
  assert.equal(positions.size, nodes.length);
  checkBounds(positions, 850, 560);
  for (const a of positions.values()) for (const b of positions.values()) {
    if (a.id !== b.id) assert.ok(Math.hypot(a.x - b.x, a.y - b.y) > 21, `${a.id}/${b.id} overlap`);
  }
  assert.ok(new Set([...positions.values()].map(p => Math.round(p.x))).size > 20);
  assert.deepEqual(layoutNotes(nodes, edges, 850, 560), positions);
  assert.deepEqual({ nodes, edges }, input);
});

test('labels are sparse and nonoverlapping; the active node keeps its title on a narrow canvas', () => {
  for (const [width, height] of [[850, 560], [300, 420]]) {
    const positions = layoutNotes(nodes, edges, width, height);
    const labels = placeLabels(nodes, edges, positions, width, height, '40');
    assert.ok(labels.some(label => label.id === '40'));
    assert.ok(labels.every(label => ['39', '40', '41'].includes(label.id)));
    assert.ok(labels.length < nodes.length / 2);
    for (const a of labels) {
      assert.ok(a.x >= 0 && a.x + a.width <= width && a.y >= 0 && a.y + a.height <= height);
      for (const b of labels) if (a.id !== b.id) {
        assert.ok(a.x >= b.x + b.width || a.x + a.width <= b.x || a.y >= b.y + b.height || a.y + a.height <= b.y);
      }
    }
  }
});

test('empty, isolated, cyclic and dangling graphs remain renderable', () => {
  assert.equal(layoutNotes([], []).size, 0);
  assert.deepEqual(layoutNotes(nodes.slice(0, 1), [], 850, 560).get('0'), { id: '0', x: 425, y: 280 });
  for (const count of [4, 80]) {
    const placed = layoutNotes(nodes.slice(0, count), [], 850, 560);
    assert.equal(placed.size, count);
    checkBounds(placed, 850, 560);
  }
  const loop = [{ from: '0', to: '1' }, { from: '1', to: '2' }, { from: '2', to: '0' }, { from: '2', to: 'missing' }];
  assert.equal(layoutNotes(nodes.slice(0, 3), loop).size, 3);
});

test('two dense connected neighborhoods remain distinct without merging any nodes', () => {
  const sample = nodes.slice(0, 12);
  const links = [];
  for (const offset of [0, 6]) for (let i = offset; i < offset + 6; i++) {
    for (let j = i + 1; j < offset + 6; j++) links.push({ from: String(i), to: String(j) });
  }
  links.push({ from: '5', to: '6' });
  const input = structuredClone({ sample, links });
  const placed = layoutNotes(sample, links, 1200, 600);
  const distance = (a, b) => Math.hypot(placed.get(a).x - placed.get(b).x, placed.get(a).y - placed.get(b).y);
  const within = [], between = [];
  for (let i = 0; i < 12; i++) for (let j = i + 1; j < 12; j++) {
    (Math.floor(i / 6) === Math.floor(j / 6) ? within : between).push(distance(String(i), String(j)));
  }
  const mean = values => values.reduce((sum, value) => sum + value, 0) / values.length;
  assert.ok(mean(between) > mean(within) * 1.5);
  assert.equal(placed.size, 12);
  assert.deepEqual({ sample, links }, input);
  assert.deepEqual(layoutNotes(sample, links, 1200, 600), placed);
});
