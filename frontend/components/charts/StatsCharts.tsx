'use client';
import {
  PieChart, Pie, Cell, Tooltip, ResponsiveContainer,
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Legend,
  LineChart, Line, Area, AreaChart,
} from 'recharts';
import type { MatchStats } from '@/lib/types';

const HOME_COLOR = '#EF4444';
const AWAY_COLOR = '#3B82F6';
const GRID_COLOR = 'rgba(79,79,186,0.15)';
const TEXT_COLOR = '#6B7280';

interface StatsChartsProps {
  stats: MatchStats;
  homeTeamName: string;
  awayTeamName: string;
}

export function PossessionDonut({ stats, homeTeamName, awayTeamName }: StatsChartsProps) {
  const data = [
    { name: homeTeamName, value: stats.possession.home },
    { name: awayTeamName, value: stats.possession.away },
  ];

  return (
    <div className="h-48">
      <ResponsiveContainer width="100%" height="100%">
        <PieChart>
          <Pie
            data={data}
            cx="50%"
            cy="50%"
            innerRadius={50}
            outerRadius={72}
            paddingAngle={3}
            dataKey="value"
            startAngle={90}
            endAngle={-270}
          >
            {data.map((_, i) => (
              <Cell key={i} fill={i === 0 ? HOME_COLOR : AWAY_COLOR} stroke="none" />
            ))}
          </Pie>
          <Tooltip
            contentStyle={{ background: '#0F0F2E', border: '1px solid rgba(79,79,186,0.3)', borderRadius: '8px', color: '#F8F9FA' }}
            formatter={(v: number) => [`${v}%`, 'Possession']}
          />
        </PieChart>
      </ResponsiveContainer>
    </div>
  );
}

export function ShotsBars({ stats, homeTeamName, awayTeamName }: StatsChartsProps) {
  const data = [
    {
      metric: 'Total Shots',
      [homeTeamName]: stats.shots.home.total,
      [awayTeamName]: stats.shots.away.total,
    },
    {
      metric: 'On Target',
      [homeTeamName]: stats.shots.home.onTarget,
      [awayTeamName]: stats.shots.away.onTarget,
    },
    {
      metric: 'xG',
      [homeTeamName]: parseFloat(stats.shots.home.xG.toFixed(2)),
      [awayTeamName]: parseFloat(stats.shots.away.xG.toFixed(2)),
    },
    {
      metric: 'Fouls',
      [homeTeamName]: stats.fouls.home,
      [awayTeamName]: stats.fouls.away,
    },
    {
      metric: 'Corners',
      [homeTeamName]: stats.corners.home,
      [awayTeamName]: stats.corners.away,
    },
  ];

  return (
    <div className="h-48">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 0, right: 0, left: -20, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke={GRID_COLOR} />
          <XAxis dataKey="metric" tick={{ fill: TEXT_COLOR, fontSize: 10 }} />
          <YAxis tick={{ fill: TEXT_COLOR, fontSize: 10 }} />
          <Tooltip
            contentStyle={{ background: '#0F0F2E', border: '1px solid rgba(79,79,186,0.3)', borderRadius: '8px', color: '#F8F9FA' }}
          />
          <Bar dataKey={homeTeamName} fill={HOME_COLOR} radius={[4, 4, 0, 0]} />
          <Bar dataKey={awayTeamName} fill={AWAY_COLOR} radius={[4, 4, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

export function MomentumLine({ stats, homeTeamName, awayTeamName }: StatsChartsProps) {
  const data = stats.momentumTimeline || [];

  return (
    <div className="h-48">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 0, right: 0, left: -20, bottom: 0 }}>
          <defs>
            <linearGradient id="homeGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor={HOME_COLOR} stopOpacity={0.3} />
              <stop offset="95%" stopColor={HOME_COLOR} stopOpacity={0} />
            </linearGradient>
            <linearGradient id="awayGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor={AWAY_COLOR} stopOpacity={0.3} />
              <stop offset="95%" stopColor={AWAY_COLOR} stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke={GRID_COLOR} />
          <XAxis
            dataKey="minute"
            tick={{ fill: TEXT_COLOR, fontSize: 10 }}
            tickFormatter={(v) => `${v}'`}
          />
          <YAxis tick={{ fill: TEXT_COLOR, fontSize: 10 }} domain={[0, 100]} />
          <Tooltip
            contentStyle={{ background: '#0F0F2E', border: '1px solid rgba(79,79,186,0.3)', borderRadius: '8px', color: '#F8F9FA' }}
            formatter={(v: number) => [`${v}%`]}
          />
          <Area type="monotone" dataKey="home" name={homeTeamName} stroke={HOME_COLOR} fill="url(#homeGrad)" strokeWidth={2} dot={false} />
          <Area type="monotone" dataKey="away" name={awayTeamName} stroke={AWAY_COLOR} fill="url(#awayGrad)" strokeWidth={2} dot={false} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

export function PassAccuracyBars({ stats, homeTeamName, awayTeamName }: StatsChartsProps) {
  const data = [
    { name: homeTeamName, accuracy: stats.passes.home.accuracy, completed: stats.passes.home.completed, total: stats.passes.home.total },
    { name: awayTeamName, accuracy: stats.passes.away.accuracy, completed: stats.passes.away.completed, total: stats.passes.away.total },
  ];

  return (
    <div className="h-32">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} layout="vertical" margin={{ top: 0, right: 20, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke={GRID_COLOR} horizontal={false} />
          <XAxis type="number" domain={[0, 100]} tick={{ fill: TEXT_COLOR, fontSize: 10 }} tickFormatter={(v) => `${v}%`} />
          <YAxis type="category" dataKey="name" tick={{ fill: TEXT_COLOR, fontSize: 10 }} width={70} />
          <Tooltip
            contentStyle={{ background: '#0F0F2E', border: '1px solid rgba(79,79,186,0.3)', borderRadius: '8px', color: '#F8F9FA' }}
            formatter={(v: number) => [`${v}%`, 'Pass Accuracy']}
          />
          <Bar dataKey="accuracy" radius={[0, 4, 4, 0]}>
            {data.map((_, i) => <Cell key={i} fill={i === 0 ? HOME_COLOR : AWAY_COLOR} />)}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
