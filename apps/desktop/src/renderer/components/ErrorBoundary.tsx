import { Component, type ReactNode, type ErrorInfo } from 'react';

interface Props {
  children: ReactNode;
  fallback?: (error: Error, reset: () => void) => ReactNode;
}

interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('[MiQroForge] React error caught by boundary:', error, info.componentStack);
  }

  reset = () => {
    this.setState({ error: null });
  };

  render() {
    const { error } = this.state;
    if (error) {
      if (this.props.fallback) {
        return this.props.fallback(error, this.reset);
      }
      return (
        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            height: '100vh',
            padding: '24px',
            fontFamily:
              'Inter, "PingFang SC", "Microsoft YaHei", ui-sans-serif, system-ui, sans-serif',
            background: 'var(--background)',
            color: 'var(--text)',
          }}
        >
          <div
            style={{
              maxWidth: '480px',
              textAlign: 'center',
              display: 'flex',
              flexDirection: 'column',
              gap: '16px',
            }}
          >
            <div
              style={{
                width: '48px',
                height: '48px',
                borderRadius: '12px',
                background: 'var(--danger-bg)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                margin: '0 auto',
                fontSize: '20px',
                color: 'var(--danger)',
                fontWeight: 700,
              }}
            >
              !
            </div>
            <div>
              <h2 style={{ margin: '0 0 8px', fontSize: '16px', fontWeight: 600 }}>
                渲染错误 / Render Error
              </h2>
              <p
                style={{
                  margin: 0,
                  fontSize: '13px',
                  color: 'var(--text-faint)',
                  lineHeight: 1.5,
                }}
              >
                {error.message}
              </p>
            </div>
            <pre
              style={{
                margin: 0,
                padding: '12px',
                background: 'var(--surface-muted)',
                borderRadius: '8px',
                fontSize: '11px',
                textAlign: 'left',
                overflowX: 'auto',
                color: 'var(--text)',
                maxHeight: '200px',
                overflowY: 'auto',
              }}
            >
              {error.stack}
            </pre>
            <button
              onClick={this.reset}
              style={{
                padding: '8px 20px',
                background: 'var(--danger)',
                color: 'white',
                border: 'none',
                borderRadius: '8px',
                fontSize: '13px',
                fontWeight: 500,
                cursor: 'pointer',
              }}
            >
              重试 / Retry
            </button>
            <p style={{ margin: 0, fontSize: '11px', color: 'var(--text-faint)' }}>
              按 Ctrl+Shift+I 打开 DevTools 查看完整错误
            </p>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
