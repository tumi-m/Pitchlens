export default function SkeletonDashboard() {
  return (
    <main className="min-h-screen bg-[#050A10] text-[#E2E8F0]">
      {/* Header Skeleton */}
      <div className="relative pt-12 pb-24 border-b border-white/10 overflow-hidden">
        <div className="absolute inset-0 bg-gradient-to-b from-[#0B1526] to-[#050A10]" />
        <div className="max-w-6xl mx-auto px-6 relative z-10">
          <div className="flex flex-col items-center">
            {/* Badge Skeleton */}
            <div className="h-6 w-24 shimmer-bg rounded-full mb-8" />
            
            <div className="flex items-center justify-center w-full max-w-2xl">
              {/* Home Team */}
              <div className="flex flex-col items-center flex-1">
                <div className="w-24 h-24 sm:w-32 sm:h-32 rounded-full shimmer-bg mb-4" />
                <div className="h-6 w-28 shimmer-bg rounded-lg" />
              </div>
              
              {/* Score */}
              <div className="px-8 sm:px-12 flex flex-col items-center">
                <div className="h-16 w-40 shimmer-bg rounded-xl" />
                <div className="h-10 w-48 shimmer-bg rounded-full mt-8" />
              </div>

              {/* Away Team */}
              <div className="flex flex-col items-center flex-1">
                <div className="w-24 h-24 sm:w-32 sm:h-32 rounded-full shimmer-bg mb-4" />
                <div className="h-6 w-28 shimmer-bg rounded-lg" />
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Tab Skeleton */}
      <div className="border-b border-white/5">
        <div className="max-w-6xl mx-auto px-6 flex gap-2 py-3">
          {[100, 60, 70, 120].map((w, i) => (
            <div key={i} className="h-8 shimmer-bg rounded-lg" style={{ width: `${w}px` }} />
          ))}
        </div>
      </div>

      {/* Content Skeleton */}
      <div className="max-w-6xl mx-auto px-6 py-12">
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-10">
          <div className="lg:col-span-2 space-y-10">
            <div className="bg-[#0B1526] rounded-3xl p-8 border border-white/5 h-80 shimmer-bg" />
            <div className="bg-[#0B1526] rounded-3xl p-8 border border-white/5 h-96 shimmer-bg" />
          </div>
          <div className="space-y-6">
            <div className="bg-[#0B1526] rounded-2xl p-6 border border-white/5 h-48 shimmer-bg" />
            <div className="bg-[#0B1526] rounded-2xl p-6 border border-white/5 h-72 shimmer-bg" />
          </div>
        </div>
      </div>
    </main>
  );
}
