import type { Metadata, Viewport } from 'next';
import './globals.css';
import { Toaster } from 'react-hot-toast';
import { AuthProvider } from '@/components/auth/AuthProvider';
import { ThemeProvider } from '@/components/ui/ThemeProvider';

export const metadata: Metadata = {
  title: { default: 'Pitchlens', template: '%s | Pitchlens' },
  description: 'Unveil the Geometry of Your Game. Advanced soccer analytics for five-a-side enthusiasts.',
  keywords: ['soccer analytics', 'five-a-side', 'xG', 'heatmap', 'football tracking'],
  openGraph: {
    title: 'Pitchlens',
    description: 'Unveil the Geometry of Your Game.',
    type: 'website',
    siteName: 'Pitchlens',
  },
};

export const viewport: Viewport = {
  themeColor: '#0A0A0F',
  colorScheme: 'dark',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark" suppressHydrationWarning>
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
      </head>
      <body className="min-h-screen bg-pitch-black text-pitch-white">
        <ThemeProvider>
          <AuthProvider>
            {children}
            <Toaster
              position="bottom-right"
              toastOptions={{
                style: {
                  background: '#1A1A4E',
                  color: '#F8F9FA',
                  border: '1px solid rgba(79,79,186,0.3)',
                  borderRadius: '12px',
                },
                success: { iconTheme: { primary: '#2ECC71', secondary: '#F8F9FA' } },
                error: { iconTheme: { primary: '#EF4444', secondary: '#F8F9FA' } },
              }}
            />
          </AuthProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
