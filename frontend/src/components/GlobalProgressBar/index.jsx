import { useEffect, useState } from 'react';
import styles from './styles.module.css';

export default function GlobalProgressBar({ active, progress, label, startTime }) {
  const [timeLeft, setTimeLeft] = useState('');

  useEffect(() => {
    if (!active || !startTime || progress === 0) {
      setTimeLeft('');
      return;
    }

    const interval = setInterval(() => {
      const elapsedMs = Date.now() - startTime;
      // Estimate total time based on progress percentage
      if (progress > 5 && progress < 100) {
        const estimatedTotalMs = (elapsedMs / progress) * 100;
        const remainingMs = estimatedTotalMs - elapsedMs;
        
        if (remainingMs > 0) {
          const secs = Math.ceil(remainingMs / 1000);
          if (secs > 60) {
            setTimeLeft(`~${Math.ceil(secs / 60)}m left`);
          } else {
            setTimeLeft(`~${secs}s left`);
          }
        } else {
          setTimeLeft('Almost done...');
        }
      }
    }, 1000);

    return () => clearInterval(interval);
  }, [active, progress, startTime]);

  if (!active) return null;

  return (
    <div className={styles.container}>
      <div className={styles.barTrack}>
        <div 
          className={styles.barFill} 
          style={{ width: `${progress}%` }} 
        />
      </div>
      <div className={styles.info}>
        <span className={styles.label}>{label || 'Processing...'}</span>
        <span className={styles.time}>{progress === 100 ? 'Complete' : timeLeft}</span>
      </div>
    </div>
  );
}
