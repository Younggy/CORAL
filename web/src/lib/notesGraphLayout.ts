import type { NoteGraphNode, NoteGraphEdge } from "./api";

const PAD = 48;

interface Placed {
  id: string;
  x: number;
  y: number;
}

// Group by connectivity for spacing only; never merge nodes or invent topic labels.
function connectionGroups(count: number, links: [number, number][]): number[] {
  const adjacency = Array.from({ length: count }, () => new Map<number, number>());
  for (const [a, b] of links) {
    if (a === b) continue;
    adjacency[a].set(b, (adjacency[a].get(b) ?? 0) + 1);
    adjacency[b].set(a, (adjacency[b].get(a) ?? 0) + 1);
  }
  const degree = adjacency.map(neighbors => [...neighbors.values()].reduce((sum, weight) => sum + weight, 0));
  const total = degree.reduce((sum, value) => sum + value, 0);
  const groups = degree.map((_, i) => i);
  const volumes = [...degree];
  if (!total) return groups;
  for (let pass = 0; pass < 30; pass++) {
    let moved = false;
    for (let i = 0; i < count; i++) {
      const old = groups[i];
      volumes[old] -= degree[i];
      const weights = new Map<number, number>();
      for (const [neighbor, weight] of adjacency[i]) {
        const group = groups[neighbor];
        weights.set(group, (weights.get(group) ?? 0) + weight);
      }
      let best = old;
      let gain = (weights.get(old) ?? 0) - degree[i] * volumes[old] / total;
      for (const [group, weight] of weights) {
        const next = weight - degree[i] * volumes[group] / total;
        if (next > gain + 1e-9) { best = group; gain = next; }
      }
      groups[i] = best;
      volumes[best] += degree[i];
      moved ||= best !== old;
    }
    if (!moved) break;
  }
  return groups;
}

/** Force-lay out a single connected component; returns local positions + size. */
function forceComponent(
  ids: string[],
  links: [number, number][],
): { pts: { id: string; x: number; y: number }[]; w: number; h: number } {
  const n = ids.length;
  if (n === 1) return { pts: [{ id: ids[0], x: 0, y: 0 }], w: 0, h: 0 };
  const groups = connectionGroups(n, links);

  const R = 240;
  const pts = ids.map((id, i) => {
    const a = (2 * Math.PI * i) / n;
    return { id, x: Math.cos(a) * R, y: Math.sin(a) * R, vx: 0, vy: 0 };
  });
  const k = Math.max(110, R / Math.sqrt(n)) * 1.7; // ideal edge length
  const ITER = 400;

  for (let it = 0; it < ITER; it++) {
    const cool = 1 - it / ITER;
    for (let i = 0; i < n; i++) {
      pts[i].vx = 0;
      pts[i].vy = 0;
    }
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        const dx = pts[i].x - pts[j].x;
        const dy = pts[i].y - pts[j].y;
        const d = Math.hypot(dx, dy) || 0.01;
        const rep = (k * k) / d * (groups[i] === groups[j] ? 1 : 1.3);
        pts[i].vx += (dx / d) * rep;
        pts[i].vy += (dy / d) * rep;
        pts[j].vx -= (dx / d) * rep;
        pts[j].vy -= (dy / d) * rep;
      }
    }
    for (const [a, b] of links) {
      const dx = pts[a].x - pts[b].x;
      const dy = pts[a].y - pts[b].y;
      const d = Math.hypot(dx, dy) || 0.01;
      const att = (d * d) / k * (groups[a] === groups[b] ? 1 : 0.3);
      pts[a].vx -= (dx / d) * att;
      pts[a].vy -= (dy / d) * att;
      pts[b].vx += (dx / d) * att;
      pts[b].vy += (dy / d) * att;
    }
    const maxStep = 30 * cool + 1;
    for (let i = 0; i < n; i++) {
      const sp = Math.hypot(pts[i].vx, pts[i].vy) || 0.01;
      const step = Math.min(sp, maxStep);
      pts[i].x += (pts[i].vx / sp) * step;
      pts[i].y += (pts[i].vy / sp) * step;
    }
  }

  const xs = pts.map((p) => p.x);
  const ys = pts.map((p) => p.y);
  const minX = Math.min(...xs);
  const minY = Math.min(...ys);
  for (const p of pts) {
    p.x -= minX;
    p.y -= minY;
  }
  return {
    pts: pts.map((p) => ({ id: p.id, x: p.x, y: p.y })),
    w: Math.max(...xs) - minX,
    h: Math.max(...ys) - minY,
  };
}

/**
 * Deterministic component-aware layout. Each connected component is force-laid
 * out on its own (so a disconnected cluster can't fling itself across the canvas
 * and crush everything else), then components are packed row-major and the whole
 * thing is fit to the viewBox. Seeded by index — no randomness, stable across
 * refreshes.
 */
export function layoutNotes(
  nodes: NoteGraphNode[],
  edges: NoteGraphEdge[],
  width = 1000,
  height = 600,
): Map<string, Placed> {
  const out = new Map<string, Placed>();
  const N = nodes.length;
  if (N === 0) return out;

  const idx = new Map(nodes.map((n, i) => [n.id, i]));

  // union-find → connected components (undirected)
  const par = nodes.map((_, i) => i);
  const find = (x: number): number => {
    while (par[x] !== x) {
      par[x] = par[par[x]];
      x = par[x];
    }
    return x;
  };
  const elinks: [number, number][] = [];
  for (const e of edges) {
    const a = idx.get(e.from);
    const b = idx.get(e.to);
    if (a != null && b != null) {
      elinks.push([a, b]);
      par[find(a)] = find(b);
    }
  }
  const groups = new Map<number, number[]>();
  for (let i = 0; i < N; i++) {
    const r = find(i);
    const g = groups.get(r);
    if (g) g.push(i);
    else groups.set(r, [i]);
  }

  // lay out each component independently
  const comps = [...groups.values()].map((members) => {
    const local = new Map(members.map((gi, li) => [gi, li]));
    const ids = members.map((gi) => nodes[gi].id);
    const links = elinks
      .filter(([a, b]) => local.has(a) && local.has(b))
      .map(([a, b]) => [local.get(a)!, local.get(b)!] as [number, number]);
    return forceComponent(ids, links);
  });

  // pack components row-major within a target row width
  comps.sort((a, b) => b.w * b.h - a.w * a.h);
  const totalArea = comps.reduce((s, c) => s + Math.max(c.w, 60) * Math.max(c.h, 60), 1);
  const targetRow = Math.sqrt(totalArea) * 1.7;
  const GAP = 90;
  let cx = 0;
  let cy = 0;
  let rowH = 0;
  for (const c of comps) {
    const w = Math.max(c.w, 30);
    const h = Math.max(c.h, 30);
    if (cx > 0 && cx + w > targetRow) {
      cx = 0;
      cy += rowH + GAP;
      rowH = 0;
    }
    for (const p of c.pts) out.set(p.id, { id: p.id, x: cx + p.x, y: cy + p.y });
    cx += w + GAP;
    rowH = Math.max(rowH, h);
  }

  // Fit positions, not text, into the available canvas.
  const placed = [...out.values()];
  const xs = placed.map((p) => p.x);
  const ys = placed.map((p) => p.y);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const usableW = Math.max(1, width - PAD * 2);
  const usableH = Math.max(1, height - PAD * 2);
  for (const p of placed) {
    p.x = maxX === minX ? width / 2 : PAD + (p.x - minX) * usableW / (maxX - minX);
    p.y = maxY === minY ? height / 2 : PAD + (p.y - minY) * usableH / (maxY - minY);
  }

  // Resolve close nodes after fitting, without forcing them into rows or columns.
  const separation = Math.min(68, Math.max(24, Math.sqrt(usableW * usableH / N) * 0.66));
  for (let iteration = 0; iteration < 100; iteration++) {
    let collisions = 0;
    for (let i = 0; i < placed.length; i++) {
      for (let j = i + 1; j < placed.length; j++) {
        const a = placed[i];
        const b = placed[j];
        const dx = b.x - a.x || 0.01;
        const dy = b.y - a.y || 0.01;
        const distance = Math.hypot(dx, dy);
        if (distance >= separation) continue;
        const push = (separation - distance) / 2;
        a.x -= dx / distance * push;
        a.y -= dy / distance * push;
        b.x += dx / distance * push;
        b.y += dy / distance * push;
        collisions++;
      }
    }
    for (const p of placed) {
      p.x = Math.max(PAD, Math.min(width - PAD, p.x));
      p.y = Math.max(PAD, Math.min(height - PAD, p.y));
    }
    if (!collisions) break;
  }
  return out;
}

export interface GraphLabel {
  id: string;
  x: number;
  y: number;
  width: number;
  height: number;
}

/** Label only where there is room; every node keeps its full title on focus. */
export function placeLabels(
  nodes: NoteGraphNode[],
  edges: NoteGraphEdge[],
  positions: Map<string, Placed>,
  width: number,
  height: number,
  active: string | null,
): GraphLabel[] {
  const degree = new Map<string, number>();
  const related = new Set([active]);
  for (const edge of edges) {
    degree.set(edge.from, (degree.get(edge.from) ?? 0) + 1);
    degree.set(edge.to, (degree.get(edge.to) ?? 0) + 1);
    if (edge.from === active) related.add(edge.to);
    if (edge.to === active) related.add(edge.from);
  }
  const priority = nodes.filter(node => !active || related.has(node.id)).sort((a, b) =>
    Number(b.id === active) - Number(a.id === active) ||
    (degree.get(b.id) ?? 0) - (degree.get(a.id) ?? 0),
  );
  const labels: GraphLabel[] = [];
  const limit = Math.max(4, Math.floor(width * height / 24000));
  const intersects = (a: GraphLabel, b: GraphLabel, gap = 5) =>
    a.x < b.x + b.width + gap && a.x + a.width + gap > b.x &&
    a.y < b.y + b.height + gap && a.y + a.height + gap > b.y;
  for (const node of priority) {
    if (labels.length >= limit) break;
    const p = positions.get(node.id);
    if (!p || p.x < 0 || p.x > width || p.y < 0 || p.y > height) continue;
    const textWidth = [...node.title].reduce((sum, char) => sum + (char.charCodeAt(0) > 255 ? 12 : 7), 0);
    const w = Math.min(node.id === active ? 320 : Math.max(100, Math.min(188, textWidth + 8)), width - 24);
    const h = node.id === active ? Math.max(48, (Math.ceil(textWidth / (w - 20)) + 1) * 17 + 12) : 40;
    const widths = node.id === active ? [w] : [...new Set([w, Math.min(w, 136)])];
    const candidates = widths.flatMap(w => [
      { x: p.x + 20, y: p.y - h / 2 },
      { x: p.x - w - 20, y: p.y - h / 2 },
      { x: p.x - w / 2, y: p.y + 20 },
      { x: p.x - w / 2, y: p.y - h - 20 },
      { x: p.x + 24, y: p.y + 20 },
      { x: p.x - w - 24, y: p.y + 20 },
      { x: p.x + 24, y: p.y - h - 20 },
      { x: p.x - w - 24, y: p.y - h - 20 },
      { x: p.x + 48, y: p.y - h / 2 },
      { x: p.x - w - 48, y: p.y - h / 2 },
      { x: p.x - w / 2, y: p.y + 56 },
      { x: p.x - w / 2, y: p.y - h - 56 },
    ].map(point => ({ id: node.id, ...point, width: w, height: h })));
    const available = candidates.find(candidate =>
      candidate.x >= 8 && candidate.y >= 8 &&
      candidate.x + candidate.width <= width - 8 && candidate.y + h <= height - 8 &&
      !labels.some(label => intersects(candidate, label)) &&
      ![...positions.values()].some(other => other.id !== node.id && (!active || related.has(other.id)) && intersects(candidate, {
        id: other.id, x: other.x - 11, y: other.y - 11, width: 22, height: 22,
      })),
    );
    if (available) labels.push(available);
    else if (node.id === active) labels.push({
      ...candidates[0],
      x: Math.max(8, Math.min(width - w - 8, candidates[0].x)),
      y: Math.max(8, Math.min(height - h - 8, candidates[0].y)),
    });
  }
  return labels;
}
