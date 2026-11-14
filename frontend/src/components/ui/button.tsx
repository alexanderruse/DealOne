import * as React from 'react';
import { Slot } from '@radix-ui/react-slot';
import { cva, type VariantProps } from 'class-variance-authority';
import { cn } from '../../lib';
const variants = cva('button', {
  variants: {
    variant: { default: 'button-primary', outline: 'button-outline', ghost: 'button-ghost' },
  },
  defaultVariants: { variant: 'default' },
});
type Props = React.ButtonHTMLAttributes<HTMLButtonElement> &
  VariantProps<typeof variants> & { asChild?: boolean };
export const Button = React.forwardRef<HTMLButtonElement, Props>(
  ({ className, variant, asChild = false, ...props }, ref) => {
    const Component = asChild ? Slot : 'button';
    return <Component className={cn(variants({ variant, className }))} ref={ref} {...props} />;
  },
);
Button.displayName = 'Button';
