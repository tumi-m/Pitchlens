import { Activity } from 'lucide-react';

export default function Footer() {
  return (
    <footer className="border-t border-white/5 bg-background/80">
      <div className="max-w-7xl mx-auto px-6 py-8 flex flex-col sm:flex-row items-center justify-between gap-4">
        <div className="flex items-center space-x-2.5 text-secondary/50">
          <Activity className="w-4 h-4" />
          <span className="text-xs font-medium tracking-wider">
            PITCHLENS &copy; {new Date().getFullYear()}
          </span>
        </div>
        <p className="text-xs text-secondary/30 font-medium">
          Built with precision. Powered by analytics.
        </p>
      </div>
    </footer>
  );
}
