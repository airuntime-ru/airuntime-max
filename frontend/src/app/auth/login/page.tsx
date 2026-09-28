"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Mail, ShieldCheck } from "lucide-react";

import { AuthShell } from "@/components/auth/auth-shell";
import { OtpInput } from "@/components/auth/otp-input";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { requestAuthCode, verifyAuthCode } from "@/lib/api";

type Step = "email" | "code";

export default function LoginPage() {
  const [step, setStep] = useState<Step>("email");
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [acceptedTerms, setAcceptedTerms] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [resendIn, setResendIn] = useState(0);
  const router = useRouter();

  useEffect(() => {
    if (resendIn <= 0) return;
    const timer = window.setTimeout(() => setResendIn((value) => value - 1), 1000);
    return () => window.clearTimeout(timer);
  }, [resendIn]);

  const sendCode = async () => {
    if (!acceptedTerms) {
      setError("Нужно согласие с офертой и политикой конфиденциальности");
      return;
    }
    setLoading(true);
    setError("");
    try {
      await requestAuthCode(email.trim());
      setStep("code");
      setResendIn(60);
      setCode("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось отправить код");
    } finally {
      setLoading(false);
    }
  };

  const onSubmitEmail = async () => {
    if (!email.trim()) return;
    await sendCode();
  };

  const onSubmitCode = async (submittedCode = code) => {
    if (submittedCode.length !== 6) return;
    setLoading(true);
    setError("");
    try {
      await verifyAuthCode(email.trim(), submittedCode);
      router.push("/app");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Неверный код");
    } finally {
      setLoading(false);
    }
  };

  return (
    <AuthShell>
      <div className="space-y-6">
        <div className="space-y-2 text-center">
          <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-[var(--ar-radius-sm)] bg-sky-50 text-[var(--ar-sky)]">
            {step === "email" ? <Mail size={22} /> : <ShieldCheck size={22} />}
          </div>
          <h1 className="text-2xl font-semibold text-[var(--ar-black)]">
            {step === "email" ? "Вход в AIRuntime" : "Проверьте почту"}
          </h1>
          <p className="text-sm leading-relaxed text-[var(--ar-mist)]">
            {step === "email"
              ? "Отправим одноразовый код. Пароль не понадобится."
              : `Код отправлен на ${email}`}
          </p>
        </div>

        {step === "email" ? (
          <div className="space-y-4">
            <Input
              placeholder="you@company.ru"
              type="email"
              autoComplete="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") void onSubmitEmail();
              }}
            />
            <label className="flex cursor-pointer items-start gap-3 text-left text-sm leading-5 text-[var(--ar-mist)]">
              <input
                type="checkbox"
                className="mt-0.5 h-4 w-4 shrink-0 rounded border-black/20 accent-[var(--ar-sky)]"
                checked={acceptedTerms}
                onChange={(event) => setAcceptedTerms(event.target.checked)}
              />
              <span>
                Я соглашаюсь с{" "}
                <Link href="/legal/offer" className="text-[var(--ar-sky)] hover:underline" target="_blank">
                  публичной офертой
                </Link>{" "}
                и{" "}
                <Link href="/legal/privacy" className="text-[var(--ar-sky)] hover:underline" target="_blank">
                  политикой конфиденциальности
                </Link>
              </span>
            </label>
            <Button
              variant="accent"
              className="w-full"
              onClick={onSubmitEmail}
              disabled={loading || !email.trim() || !acceptedTerms}
            >
              {loading ? "Отправляем..." : "Получить код"}
            </Button>
          </div>
        ) : (
          <form
            className="space-y-4"
            onSubmit={(event) => {
              event.preventDefault();
              void onSubmitCode();
            }}
          >
            <OtpInput
              value={code}
              onChange={setCode}
              onComplete={(value) => void onSubmitCode(value)}
              disabled={loading}
              autoFocus
            />
            <Button
              type="submit"
              variant="accent"
              className="w-full"
              disabled={loading || code.length !== 6}
            >
              {loading ? "Входим..." : "Войти"}
            </Button>
            <div className="flex items-center justify-between gap-3 text-sm">
              <button
                type="button"
                className="text-[var(--ar-mist)] hover:text-[var(--ar-black)]"
                onClick={() => {
                  setStep("email");
                  setCode("");
                  setError("");
                }}
              >
                Другая почта
              </button>
              <button
                type="button"
                className="text-[var(--ar-sky)] hover:underline disabled:opacity-40"
                disabled={resendIn > 0 || loading}
                onClick={() => void sendCode()}
              >
                {resendIn > 0 ? `Повтор через ${resendIn}с` : "Отправить снова"}
              </button>
            </div>
          </form>
        )}

        {error ? (
          <p className="rounded-[var(--ar-radius-sm)] border border-rose-200 bg-rose-50 px-3 py-2 text-center text-sm text-rose-700">
            {error}
          </p>
        ) : null}
      </div>
    </AuthShell>
  );
}
