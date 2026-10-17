import BottomNav from "@/components/BottomNav";
import OfflineBanner from "@/components/OfflineBanner";
import PushPrompt from "@/components/PushPrompt";
import Sidebar from "@/components/Sidebar";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen">
      <Sidebar />
      <div className="flex-1 min-w-0 flex flex-col">
        <OfflineBanner />
        <PushPrompt />
        {/* phones: room for the fixed bottom bar (+ the home-indicator inset on iPhone) */}
        <main className="flex-1 p-4 md:p-6 overflow-x-auto pb-[calc(5rem+env(safe-area-inset-bottom))] md:pb-6">{children}</main>
      </div>
      <BottomNav />
    </div>
  );
}
