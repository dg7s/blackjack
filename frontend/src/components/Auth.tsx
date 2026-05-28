/**
 * src/components/Auth.tsx
 * =======================
 * Login + Registration screen.
 * Manages its own form state — no global store involvement until
 * a successful response calls store.setAuth().
 */

import { useState, type FormEvent } from 'react';
import { useMutation } from '@tanstack/react-query';
import { login, register } from '../api';
import { useAppStore } from '../store';
import type { ApiError } from '../types';
import styles from './Auth.module.css';

type Tab = 'login' | 'register';

export default function Auth() {
  const [tab, setTab] = useState<Tab>('login');

  return (
    <div className={styles.page}>
      {/* ── Background ornament ── */}
      <div className={styles.ornamentTL} aria-hidden />
      <div className={styles.ornamentBR} aria-hidden />

      <div className={styles.card}>
        {/* Header */}
        <div className={styles.header}>
          <div className={styles.suitRow} aria-hidden>♠ ♥ ♣ ♦</div>
          <h1 className={styles.title}>Royal Table</h1>
          <p className={styles.subtitle}>Single-player Blackjack</p>
        </div>

        {/* Tabs */}
        <div className={styles.tabs} role="tablist">
          <button
            role="tab"
            aria-selected={tab === 'login'}
            className={`${styles.tab} ${tab === 'login' ? styles.tabActive : ''}`}
            onClick={() => setTab('login')}
          >
            Sign In
          </button>
          <button
            role="tab"
            aria-selected={tab === 'register'}
            className={`${styles.tab} ${tab === 'register' ? styles.tabActive : ''}`}
            onClick={() => setTab('register')}
          >
            Register
          </button>
        </div>

        {/* Forms */}
        {tab === 'login'    && <LoginForm />}
        {tab === 'register' && <RegisterForm onSuccess={() => setTab('login')} />}
      </div>
    </div>
  );
}

/* ── Login form ─────────────────────────────────────────────────────────────── */

function LoginForm() {
  const setAuth = useAppStore(s => s.setAuth);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');

  const { mutate, isPending, error } = useMutation({
    mutationFn: () => login({ username, password }),
    onSuccess: (data) => setAuth(data.user, data.token),
  });

  const apiError = error as ApiError | null;

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    mutate();
  };

  return (
    <form className={styles.form} onSubmit={handleSubmit} noValidate>
      {apiError && (
        <div className={styles.errorBanner} role="alert">
          {apiError.message}
        </div>
      )}

      <Field
        label="Username"
        id="login-username"
        type="text"
        value={username}
        onChange={setUsername}
        autoComplete="username"
        required
      />
      <Field
        label="Password"
        id="login-password"
        type="password"
        value={password}
        onChange={setPassword}
        autoComplete="current-password"
        required
      />

      <button className={styles.submitBtn} type="submit" disabled={isPending}>
        {isPending ? <Spinner /> : 'Enter the Table'}
      </button>
    </form>
  );
}

/* ── Register form ──────────────────────────────────────────────────────────── */

interface RegisterFormProps {
  onSuccess: () => void;
}

function RegisterForm({ onSuccess }: RegisterFormProps) {
  const [username, setUsername]             = useState('');
  const [email, setEmail]                   = useState('');
  const [password, setPassword]             = useState('');
  const [passwordConfirm, setPasswordConfirm] = useState('');

  const { mutate, isPending, error } = useMutation({
    mutationFn: () =>
      register({ username, email, password, password_confirm: passwordConfirm }),
    onSuccess,
  });

  const apiError = error as ApiError | null;

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    mutate();
  };

  return (
    <form className={styles.form} onSubmit={handleSubmit} noValidate>
      {apiError && !apiError.fieldErrors && (
        <div className={styles.errorBanner} role="alert">
          {apiError.message}
        </div>
      )}

      <Field
        label="Username"
        id="reg-username"
        type="text"
        value={username}
        onChange={setUsername}
        autoComplete="username"
        error={apiError?.fieldErrors?.username?.[0]}
        required
      />
      <Field
        label="Email (optional)"
        id="reg-email"
        type="email"
        value={email}
        onChange={setEmail}
        autoComplete="email"
      />
      <Field
        label="Password"
        id="reg-password"
        type="password"
        value={password}
        onChange={setPassword}
        autoComplete="new-password"
        error={apiError?.fieldErrors?.password?.[0]}
        required
      />
      <Field
        label="Confirm Password"
        id="reg-confirm"
        type="password"
        value={passwordConfirm}
        onChange={setPasswordConfirm}
        autoComplete="new-password"
        error={apiError?.fieldErrors?.password_confirm?.[0]}
        required
      />

      <p className={styles.startBalance}>
        You'll start with <strong>1,000 chips</strong>.
      </p>

      <button className={styles.submitBtn} type="submit" disabled={isPending}>
        {isPending ? <Spinner /> : 'Create Account'}
      </button>
    </form>
  );
}

/* ── Shared sub-components ──────────────────────────────────────────────────── */

interface FieldProps {
  label:        string;
  id:           string;
  type:         string;
  value:        string;
  onChange:     (v: string) => void;
  error?:       string;
  required?:    boolean;
  autoComplete?: string;
}

function Field({ label, id, type, value, onChange, error, required, autoComplete }: FieldProps) {
  return (
    <div className={styles.field}>
      <label className={styles.label} htmlFor={id}>{label}</label>
      <input
        className={`${styles.input} ${error ? styles.inputError : ''}`}
        id={id}
        type={type}
        value={value}
        onChange={e => onChange(e.target.value)}
        required={required}
        autoComplete={autoComplete}
        spellCheck={false}
      />
      {error && <span className={styles.fieldError}>{error}</span>}
    </div>
  );
}

function Spinner() {
  return <span className={styles.spinner} aria-label="Loading" />;
}
