import Link from "next/link";

export default function Home() {
  return (
    <main className="landingwrap">
      <h1>LowSpy</h1>
      <p>Radar de infoprodutos com demanda real, minerados do TikTok e Meta Ads.</p>
      <div className="landingcta">
        <Link href="/signup" className="primary">criar conta</Link>
        <Link href="/login" className="secondary">entrar</Link>
      </div>
    </main>
  );
}
