import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";
import type { NoteGraphNode, NoteGraphEdge } from "../lib/api";
import { layoutNotes, placeLabels } from "../lib/notesGraphLayout";
import type { GraphLabel } from "../lib/notesGraphLayout";

interface Point { x: number; y: number }
const EDGE_STYLE = {
  references: { color: "var(--graph-reference)", dash: undefined },
  supersedes: { color: "var(--graph-supersedes)", dash: "5 4" },
  refutes: { color: "var(--graph-refutes)", dash: undefined },
};

function radius(node: NoteGraphNode): number {
  const confidence = node.confidence;
  if (typeof confidence === "number") return confidence < 0.4 ? 4 : confidence < 0.7 ? 6 : 8;
  return confidence === "high" ? 8 : confidence === "medium" ? 6 : confidence === "low" ? 4 : 5;
}

function edgePath(a: Point, b: Point, targetRadius: number): string {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const distance = Math.hypot(dx, dy) || 1;
  const end = { x: b.x - dx / distance * (targetRadius + 5), y: b.y - dy / distance * (targetRadius + 5) };
  const curve = Math.min(22, distance * 0.08);
  return `M${a.x},${a.y} Q${(a.x + end.x) / 2 - dy / distance * curve},${(a.y + end.y) / 2 + dx / distance * curve} ${end.x},${end.y}`;
}

function labelLeader(point: Point, label: GraphLabel): string {
  const x = Math.max(label.x, Math.min(label.x + label.width, point.x));
  const y = Math.max(label.y, Math.min(label.y + label.height, point.y));
  const distance = Math.hypot(x - point.x, y - point.y) || 1;
  return `M${point.x + (x - point.x) / distance * 12},${point.y + (y - point.y) / distance * 12} L${x},${y}`;
}

export default function NotesGraph({ nodes, edges, selected, onSelect }: {
  nodes: NoteGraphNode[];
  edges: NoteGraphEdge[];
  selected: string | null;
  onSelect: (id: string) => void;
}) {
  const canvasRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 900, height: 560 });
  const [camera, setCamera] = useState({ x: 0, y: 0, scale: 1 });
  const [overrides, setOverrides] = useState<Map<string, Point>>(() => new Map());
  const [hovered, setHovered] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const drag = useRef<{ pointerId: number; id: string | null; start: Point; origin: Point } | null>(null);
  const moved = useRef(false);

  useEffect(() => {
    const element = canvasRef.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      if (width && height) setSize({ width, height });
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const layout = useMemo(() => layoutNotes(nodes, edges, size.width, size.height), [nodes, edges, size]);
  const positions = useMemo(() => new Map([...layout].map(([id, point]) => {
    const p = overrides.get(id) ?? point;
    return [id, { id, x: p.x * camera.scale + camera.x, y: p.y * camera.scale + camera.y }];
  })), [layout, overrides, camera]);
  const active = hovered ?? selected;
  const neighbors = useMemo(() => {
    const ids = new Set([active]);
    for (const edge of edges) {
      if (edge.from === active) ids.add(edge.to);
      if (edge.to === active) ids.add(edge.from);
    }
    return ids;
  }, [active, edges]);
  const labels = useMemo(() => new Map(placeLabels(nodes, edges, positions, size.width, size.height, active).map(label => [label.id, label])), [nodes, edges, positions, size, active]);
  const byId = useMemo(() => new Map(nodes.map(node => [node.id, node])), [nodes]);
  const activeLabel = active ? labels.get(active) : undefined;

  const zoom = useCallback((factor: number, x = size.width / 2, y = size.height / 2) => {
    setCamera(previous => {
      const scale = Math.max(0.5, Math.min(3, previous.scale * factor));
      const ratio = scale / previous.scale;
      return { scale, x: x - (x - previous.x) * ratio, y: y - (y - previous.y) * ratio };
    });
  }, [size]);

  useEffect(() => {
    const element = canvasRef.current;
    if (!element) return;
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      const rect = element.getBoundingClientRect();
      zoom(Math.exp(-event.deltaY * 0.002), event.clientX - rect.left, event.clientY - rect.top);
    };
    element.addEventListener("wheel", onWheel, { passive: false });
    return () => element.removeEventListener("wheel", onWheel);
  }, [zoom]);

  function reset() {
    setCamera({ x: 0, y: 0, scale: 1 });
    setOverrides(new Map());
  }

  function startDrag(event: ReactPointerEvent<SVGElement>, id: string | null) {
    if (event.button !== 0 || !event.isPrimary) return;
    event.stopPropagation();
    const origin = id ? overrides.get(id) ?? layout.get(id) : camera;
    if (!origin) return;
    moved.current = false;
    drag.current = { pointerId: event.pointerId, id, start: { x: event.clientX, y: event.clientY }, origin };
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function moveDrag(event: ReactPointerEvent<SVGSVGElement>) {
    const current = drag.current;
    if (!current || current.pointerId !== event.pointerId) return;
    const dx = event.clientX - current.start.x;
    const dy = event.clientY - current.start.y;
    if (Math.hypot(dx, dy) < 4 && !moved.current) return;
    moved.current = true;
    setDragging(true);
    if (current.id) {
      setOverrides(previous => new Map(previous).set(current.id!, {
        x: current.origin.x + dx / camera.scale,
        y: current.origin.y + dy / camera.scale,
      }));
    } else setCamera(previous => ({ ...previous, x: current.origin.x + dx, y: current.origin.y + dy }));
  }

  function stopDrag() {
    drag.current = null;
    setDragging(false);
  }

  return (
    <div className="notes-graph">
      <div className="graph-toolbar">
        <span><strong>{nodes.length}</strong> notes <span className="graph-count-separator">/</span> <strong>{edges.length}</strong> connections</span>
        <div className="graph-controls" aria-label="Graph view controls">
          <button type="button" onClick={() => zoom(1 / 1.25)} aria-label="Zoom graph out" title="Zoom out">−</button>
          <span aria-live="polite">{Math.round(camera.scale * 100)}%</span>
          <button type="button" onClick={() => zoom(1.25)} aria-label="Zoom graph in" title="Zoom in">+</button>
          <button type="button" onClick={reset} aria-label="Reset graph view" title="Reset view and node positions">
            <svg width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.4" aria-hidden="true"><path d="M7 3H3v4m10-4h4v4M3 13v4h4m10-4v4h-4" /></svg>
          </button>
        </div>
      </div>
      <div ref={canvasRef} className={`graph-canvas ${dragging ? "is-dragging" : ""}`}>
        {nodes.length === 0 && <p className="graph-empty">No notes to graph yet.</p>}
        <svg width="100%" height="100%" aria-label="Notes graph" tabIndex={0}
          onPointerDown={event => startDrag(event, null)}
          onPointerMove={moveDrag} onPointerUp={stopDrag} onPointerCancel={stopDrag}
          onLostPointerCapture={stopDrag}
          onDoubleClick={event => { if (!(event.target as Element).closest("[data-node-id], [data-label-id]")) reset(); }}
          onKeyDown={event => {
            if (event.key === "+" || event.key === "=") { event.preventDefault(); zoom(1.25); }
            if (event.key === "-") { event.preventDefault(); zoom(1 / 1.25); }
            if (event.key === "0") reset();
          }}>
          <defs>
            {Object.entries(EDGE_STYLE).map(([kind, style]) => (
              <marker key={kind} id={`ng-arrow-${kind}`} markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto" markerUnits="userSpaceOnUse">
                <path d="M0,0 L6,3 L0,6 Z" fill={style.color} />
              </marker>
            ))}
          </defs>
          <g className="graph-edges" pointerEvents="none">
            {edges.map((edge, i) => {
              const a = positions.get(edge.from);
              const b = positions.get(edge.to);
              if (!a || !b) return null;
              const connected = active === edge.from || active === edge.to;
              const style = EDGE_STYLE[edge.kind] ?? EDGE_STYLE.references;
              return <path key={i} d={edgePath(a, b, radius(byId.get(edge.to)!))} fill="none"
                stroke={connected && edge.kind === "references" ? "var(--color-accent)" : style.color}
                strokeWidth={connected ? 1.6 : edge.kind === "references" ? 0.8 : 1.4} strokeDasharray={style.dash}
                markerEnd={`url(#ng-arrow-${edge.kind})`} opacity={active ? connected ? 0.9 : 0.055 : edge.kind === "references" ? 0.24 : 0.85} />;
            })}
          </g>
          {nodes.map(node => {
            const p = positions.get(node.id);
            if (!p) return null;
            const emphasized = node.id === active;
            return (
              <g key={node.id} transform={`translate(${p.x},${p.y})`} data-node-id={node.id}
                data-status={node.status ?? "unknown"} className="graph-node"
                role="button" tabIndex={0} aria-label={node.title} aria-pressed={selected === node.id}
                opacity={active && !neighbors.has(node.id) ? 0.25 : 1}
                onPointerDown={event => startDrag(event, node.id)}
                onPointerEnter={() => { if (!drag.current) setHovered(node.id); }}
                onPointerLeave={() => setHovered(null)}
                onFocus={() => {
                  setHovered(node.id);
                  if (p.x < 20 || p.x > size.width - 20 || p.y < 20 || p.y > size.height - 20) {
                    setCamera(previous => ({ ...previous, x: previous.x + size.width / 2 - p.x, y: previous.y + size.height / 2 - p.y }));
                  }
                }}
                onBlur={() => setHovered(null)}
                onClick={() => { if (!moved.current) onSelect(node.id); }}
                onKeyDown={event => {
                  if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onSelect(node.id); }
                }}>
                <title>{node.title}</title>
                <circle r={17} fill="transparent" />
                {emphasized && <circle r={radius(node) + 6} className="graph-node-halo" />}
                <circle r={radius(node) + 2.5} className="graph-node-ring" />
                <circle r={radius(node)} className="graph-node-dot" />
              </g>
            );
          })}
          <g className="graph-labels" aria-hidden="true">
            {[...labels.values()].filter(label => label.id !== active).map(label => (
              <g key={label.id} data-label-id={label.id} className="graph-label-target"
                onPointerDown={event => startDrag(event, label.id)}
                onClick={() => { if (!moved.current) onSelect(label.id); }}>
                <path d={labelLeader(positions.get(label.id)!, label)} className="graph-label-leader" fill="none" />
                <foreignObject x={label.x} y={label.y} width={label.width} height={label.height} className="graph-label-box">
                  <div className="graph-label"><span className="graph-label-text">{byId.get(label.id)?.title}</span></div>
                </foreignObject>
              </g>
            ))}
          </g>
          {/* Paint the active title above every node without changing keyboard order. */}
          {activeLabel && <path d={labelLeader(positions.get(activeLabel.id)!, activeLabel)} className="graph-label-leader" fill="none" />}
          {activeLabel && <foreignObject x={activeLabel.x} y={activeLabel.y} width={activeLabel.width} height={activeLabel.height} className="graph-label-box" pointerEvents="none">
            <div className="graph-label is-active">{byId.get(activeLabel.id)?.title}</div>
          </foreignObject>}
        </svg>
        <span className="graph-hint">Drag to explore · Scroll to zoom</span>
      </div>
      <div className="graph-legend">
        <div className="graph-legend-group">
          {[["confirmed", "--graph-confirmed"], ["refuted", "--graph-refutes"], ["untested", "--graph-untested"], ["no status", "--graph-reference"]].map(([label, variable]) => (
            <span key={label}><i className="graph-legend-dot" style={{ background: `var(${variable})` }} />{label}</span>
          ))}
        </div>
        <div className="graph-legend-group">
          {Object.entries(EDGE_STYLE).map(([kind, style]) => <span key={kind}>
            <svg width="23" height="8" aria-hidden="true"><path d="M0 4h18m-4-3 5 3-5 3" fill="none" stroke={style.color} strokeWidth="1.3" strokeDasharray={style.dash} /></svg>{kind}
          </span>)}
        </div>
        <span className="graph-legend-meta">Size = confidence · Arrow = direction</span>
      </div>
    </div>
  );
}
