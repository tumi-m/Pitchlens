'use client';
import { useRef, useEffect, useState } from 'react';
import * as d3 from 'd3';
import type { PlayerHeatmap, VoronoiFrame, PassNetwork } from '@/lib/types';

// Five-a-side pitch: 42m × 25m
const PITCH_WIDTH = 42;
const PITCH_HEIGHT = 25;

type ViewMode = 'heatmap' | 'voronoi' | 'passnetwork';

interface PitchSVGProps {
  heatmaps?: PlayerHeatmap[];
  voronoi?: VoronoiFrame[];
  passNetwork?: PassNetwork;
  mode: ViewMode;
  homeColor?: string;
  awayColor?: string;
  className?: string;
}

export function PitchSVG({
  heatmaps = [],
  voronoi = [],
  passNetwork,
  mode,
  homeColor = '#EF4444',
  awayColor = '#3B82F6',
  className = '',
}: PitchSVGProps) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [dimensions, setDimensions] = useState({ width: 700, height: 420 });

  useEffect(() => {
    const el = svgRef.current?.parentElement;
    if (!el) return;
    const obs = new ResizeObserver(([entry]) => {
      const w = entry.contentRect.width;
      setDimensions({ width: w, height: w * (PITCH_HEIGHT / PITCH_WIDTH) });
    });
    obs.observe(el);
    return () => obs.disconnect();
  }, []);

  const { width, height } = dimensions;
  const scaleX = (x: number) => (x / PITCH_WIDTH) * width;
  const scaleY = (y: number) => (y / PITCH_HEIGHT) * height;

  useEffect(() => {
    const svg = d3.select(svgRef.current);
    svg.selectAll('.dynamic-layer').remove();
    const layer = svg.append('g').attr('class', 'dynamic-layer');

    if (mode === 'heatmap' && heatmaps.length > 0) {
      renderHeatmap(layer, heatmaps, scaleX, scaleY, homeColor, awayColor);
    } else if (mode === 'voronoi' && voronoi.length > 0) {
      renderVoronoi(layer, voronoi[0], scaleX, scaleY, width, height, homeColor, awayColor);
    } else if (mode === 'passnetwork' && passNetwork) {
      renderPassNetwork(layer, passNetwork, scaleX, scaleY, homeColor, awayColor);
    }
  }, [mode, heatmaps, voronoi, passNetwork, width, height]);

  return (
    <svg
      ref={svgRef}
      viewBox={`0 0 ${width} ${height}`}
      className={`w-full rounded-xl ${className}`}
      aria-label="Five-a-side pitch visualization"
    >
      <defs>
        <filter id="blur-heat">
          <feGaussianBlur stdDeviation="8" />
        </filter>
        <radialGradient id="field-grad" cx="50%" cy="50%" r="60%">
          <stop offset="0%" stopColor="#2D5A27" />
          <stop offset="100%" stopColor="#1A3D1A" />
        </radialGradient>
      </defs>

      {/* Field background */}
      <rect width={width} height={height} fill="url(#field-grad)" rx="8" />

      {/* Pitch stripes */}
      {Array.from({ length: 7 }).map((_, i) => (
        <rect
          key={i}
          x={(i * width) / 7}
          y={0}
          width={width / 7}
          height={height}
          fill={i % 2 === 0 ? 'rgba(0,0,0,0.06)' : 'transparent'}
        />
      ))}

      {/* Pitch markings */}
      <PitchMarkings w={width} h={height} sx={scaleX} sy={scaleY} />
    </svg>
  );
}

function PitchMarkings({ w, h, sx, sy }: { w: number; h: number; sx: (x: number) => number; sy: (y: number) => number }) {
  const stroke = 'rgba(255,255,255,0.5)';
  const strokeW = 1.5;

  return (
    <g stroke={stroke} strokeWidth={strokeW} fill="none">
      {/* Boundary */}
      <rect x={2} y={2} width={w - 4} height={h - 4} rx={4} />

      {/* Halfway line */}
      <line x1={w / 2} y1={2} x2={w / 2} y2={h - 2} />

      {/* Centre circle */}
      <circle cx={w / 2} cy={h / 2} r={Math.min(w, h) * 0.1} />
      <circle cx={w / 2} cy={h / 2} r={2} fill={stroke} />

      {/* Goal areas (6m boxes) */}
      <rect x={2} y={sy(8)} width={sx(5)} height={sy(9)} />
      <rect x={w - sx(5) - 2} y={sy(8)} width={sx(5)} height={sy(9)} />

      {/* Goals */}
      <rect x={0} y={sy(10)} width={8} height={sy(5)} fill="rgba(255,255,255,0.15)" stroke={stroke} />
      <rect x={w - 8} y={sy(10)} width={8} height={sy(5)} fill="rgba(255,255,255,0.15)" stroke={stroke} />

      {/* Penalty spots */}
      <circle cx={sx(6)} cy={h / 2} r={3} fill={stroke} />
      <circle cx={w - sx(6)} cy={h / 2} r={3} fill={stroke} />
    </g>
  );
}

function renderHeatmap(
  layer: d3.Selection<SVGGElement, unknown, null, undefined>,
  heatmaps: PlayerHeatmap[],
  scaleX: (x: number) => number,
  scaleY: (y: number) => number,
  homeColor: string,
  awayColor: string
) {
  heatmaps.forEach((ph) => {
    const color = ph.teamSide === 'home' ? homeColor : awayColor;
    const grad = layer.append('defs').append('radialGradient').attr('id', `heat-${ph.playerId}`);
    grad.append('stop').attr('offset', '0%').attr('stop-color', color).attr('stop-opacity', 0.8);
    grad.append('stop').attr('offset', '100%').attr('stop-color', color).attr('stop-opacity', 0);

    ph.positions.forEach(({ x, y, intensity }) => {
      layer
        .append('circle')
        .attr('cx', scaleX(x))
        .attr('cy', scaleY(y))
        .attr('r', 20 * intensity)
        .attr('fill', color)
        .attr('opacity', intensity * 0.35)
        .attr('filter', 'url(#blur-heat)');
    });
  });
}

function renderVoronoi(
  layer: d3.Selection<SVGGElement, unknown, null, undefined>,
  frame: VoronoiFrame,
  scaleX: (x: number) => number,
  scaleY: (y: number) => number,
  width: number,
  height: number,
  homeColor: string,
  awayColor: string
) {
  // Placeholder voronoi cells (real implementation uses scipy output coordinates)
  frame.zones.forEach((zone, i) => {
    const color = zone.teamSide === 'home' ? homeColor : awayColor;
    layer
      .append('rect')
      .attr('x', (i % 5) * (width / 5))
      .attr('y', Math.floor(i / 5) * (height / 3))
      .attr('width', width / 5)
      .attr('height', height / 3)
      .attr('fill', color)
      .attr('opacity', 0.15)
      .attr('stroke', color)
      .attr('stroke-width', 0.5)
      .attr('stroke-opacity', 0.3);
  });
}

function renderPassNetwork(
  layer: d3.Selection<SVGGElement, unknown, null, undefined>,
  network: PassNetwork,
  scaleX: (x: number) => number,
  scaleY: (y: number) => number,
  homeColor: string,
  awayColor: string
) {
  // Draw edges
  network.edges.forEach((edge) => {
    const from = network.nodes.find((n) => n.playerId === edge.from);
    const to = network.nodes.find((n) => n.playerId === edge.to);
    if (!from || !to) return;

    layer
      .append('line')
      .attr('x1', scaleX(from.x))
      .attr('y1', scaleY(from.y))
      .attr('x2', scaleX(to.x))
      .attr('y2', scaleY(to.y))
      .attr('stroke', from.teamSide === 'home' ? homeColor : awayColor)
      .attr('stroke-width', Math.max(1, edge.count / 5))
      .attr('stroke-opacity', 0.4);
  });

  // Draw nodes
  network.nodes.forEach((node) => {
    const color = node.teamSide === 'home' ? homeColor : awayColor;
    const r = 6 + node.involvement * 0.8;

    const g = layer.append('g')
      .attr('transform', `translate(${scaleX(node.x)},${scaleY(node.y)})`);

    g.append('circle').attr('r', r).attr('fill', color).attr('opacity', 0.85);
    g.append('text')
      .attr('text-anchor', 'middle')
      .attr('dy', '0.35em')
      .attr('fill', 'white')
      .attr('font-size', '8px')
      .attr('font-weight', 'bold')
      .text(node.name.split(' ')[0]?.[0] ?? '?');
  });
}
