import { Component, Suspense, lazy } from "react";
import type { ComponentProps, ComponentType, LazyExoticComponent, ReactNode } from "react";
import { Alert, Button, Spin } from "antd";

interface LoadBoundaryProps {
  children: ReactNode;
  label: string;
  onRetry: () => void;
}

class LoadBoundary extends Component<LoadBoundaryProps, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    if (this.state.failed) {
      return (
        <Alert
          type="error"
          showIcon
          message={`${this.props.label}加载失败`}
          description="请检查网络后重试。"
          action={<Button onClick={this.props.onRetry}>重试加载</Button>}
        />
      );
    }
    return this.props.children;
  }
}

interface AsyncContentProps<P extends object> {
  load: () => Promise<{ default: ComponentType<P> }>;
  componentProps: P;
  label: string;
}

/** 失败后重新创建 lazy 实例,让重试实际重新加载模块。load 应在模块顶层声明。 */
interface AsyncContentState<P extends object> {
  Content: LazyExoticComponent<ComponentType<P>>;
  attempt: number;
}

export default class AsyncContent<P extends object> extends Component<
  AsyncContentProps<P>,
  AsyncContentState<P>
> {
  constructor(props: AsyncContentProps<P>) {
    super(props);
    this.state = { Content: lazy(props.load), attempt: 0 };
  }

  retry = () => {
    this.setState(({ attempt }) => ({ Content: lazy(this.props.load), attempt: attempt + 1 }));
  };

  render() {
    const { componentProps, label } = this.props;
    const { Content, attempt } = this.state;
    const props = componentProps as ComponentProps<typeof Content>;
    return (
      <LoadBoundary key={attempt} label={label} onRetry={this.retry}>
        <Suspense
          fallback={
            <div role="status" aria-label={`${label}加载中`} style={{ padding: 32 }}>
              <Spin />
            </div>
          }
        >
          <Content key={attempt} {...props} />
        </Suspense>
      </LoadBoundary>
    );
  }
}
