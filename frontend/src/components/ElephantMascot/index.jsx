import { useState, useEffect, useRef } from 'react';
import ChatPanel from '../ChatPanel';
import styles from './styles.module.css';

function ElephantMascot({ repoId }) {
  const [chatOpen, setChatOpen] = useState(false);
  const [mousePos, setMousePos] = useState({ x: 0, y: 0 });
  const containerRef = useRef(null);

  // Track mouse movements globally
  useEffect(() => {
    const handleMouseMove = (e) => {
      setMousePos({ x: e.clientX, y: e.clientY });
    };
    window.addEventListener('mousemove', handleMouseMove);
    return () => window.removeEventListener('mousemove', handleMouseMove);
  }, []);

  // Calculate eye offsets based on mouse position relative to mascot
  const calculateEyeOffset = (eyeCenterX, eyeCenterY) => {
    if (!containerRef.current) return { x: 0, y: 0 };
    
    // Get absolute position of the eye center
    const rect = containerRef.current.getBoundingClientRect();
    // SVGs viewBox is 120x120, scaled to the actual width
    const scale = rect.width / 120;
    
    const absoluteEyeX = rect.left + eyeCenterX * scale;
    const absoluteEyeY = rect.top + eyeCenterY * scale;

    const dx = mousePos.x - absoluteEyeX;
    const dy = mousePos.y - absoluteEyeY;
    const distance = Math.sqrt(dx * dx + dy * dy);
    
    // Max distance the pupil can move from center
    const maxOffset = 3.5; 
    
    if (distance === 0) return { x: 0, y: 0 };
    
    const force = Math.min(distance / 200, 1); // Normalize force based on distance
    const moveX = (dx / distance) * maxOffset * force;
    const moveY = (dy / distance) * maxOffset * force;
    
    return { x: moveX, y: moveY };
  };

  const leftEyeOffset = calculateEyeOffset(45, 50);
  const rightEyeOffset = calculateEyeOffset(75, 50);

  return (
    <div className={styles.wrapper}>
      {/* Chat Popover */}
      {chatOpen && (
        <div className={styles.chatContainer}>
          <div className={styles.chatHeader}>
            <span className={styles.chatTitle}>Codebase Assistant</span>
            <button className={styles.closeBtn} onClick={() => setChatOpen(false)}>×</button>
          </div>
          <div className={styles.chatContent}>
            <ChatPanel repoId={repoId} />
          </div>
        </div>
      )}

      {/* Mascot */}
      <div 
        ref={containerRef}
        className={`${styles.mascotContainer} ${chatOpen ? styles.mascotActive : ''}`} 
        onClick={() => setChatOpen(!chatOpen)}
        title="Ask me anything!"
      >
        <svg 
          viewBox="0 0 120 120" 
          width="80" 
          height="80" 
          xmlns="http://www.w3.org/2000/svg"
          className={styles.elephantSvg}
        >
          {/* Ears */}
          <circle cx="20" cy="50" r="25" fill="var(--elephant-ear)" />
          <circle cx="100" cy="50" r="25" fill="var(--elephant-ear)" />
          
          {/* Head */}
          <rect x="35" y="30" width="50" height="55" rx="20" fill="var(--elephant-skin)" />
          
          {/* Trunk */}
          <path d="M 50 75 Q 60 115 70 75" stroke="var(--elephant-skin)" strokeWidth="16" fill="none" strokeLinecap="round" />
          <path d="M 52 85 Q 60 105 68 85" stroke="var(--elephant-crease)" strokeWidth="2" fill="none" />
          
          {/* Tusks */}
          <path d="M 40 75 Q 35 90 30 85" stroke="#FFFFFF" strokeWidth="4" fill="none" strokeLinecap="round" />
          <path d="M 80 75 Q 85 90 90 85" stroke="#FFFFFF" strokeWidth="4" fill="none" strokeLinecap="round" />
          
          {/* Eye Whites */}
          <circle cx="45" cy="50" r="8" fill="#FFFFFF" />
          <circle cx="75" cy="50" r="8" fill="#FFFFFF" />
          
          {/* Pupils */}
          <circle 
            cx={45 + leftEyeOffset.x} 
            cy={50 + leftEyeOffset.y} 
            r="3.5" 
            fill="#1E293B" 
            style={{ transition: 'cx 0.1s ease-out, cy 0.1s ease-out' }}
          />
          <circle 
            cx={75 + rightEyeOffset.x} 
            cy={50 + rightEyeOffset.y} 
            r="3.5" 
            fill="#1E293B" 
            style={{ transition: 'cx 0.1s ease-out, cy 0.1s ease-out' }}
          />
        </svg>
      </div>
    </div>
  );
}

export default ElephantMascot;
