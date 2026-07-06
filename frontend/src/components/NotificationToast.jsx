import React, { useEffect } from 'react';
import { useNotificationStore } from '../store';
import { X, CheckCircle, AlertTriangle, Info, XCircle } from 'lucide-react';

const icons = {
  success: <CheckCircle className="toast-icon success" />,
  error: <XCircle className="toast-icon error" />,
  warning: <AlertTriangle className="toast-icon warning" />,
  info: <Info className="toast-icon info" />,
};

const bgColors = {
  success: 'toast-success',
  error: 'toast-error',
  warning: 'toast-warning',
  info: 'toast-info',
};

const Toast = ({ notification }) => {
  const removeNotification = useNotificationStore(state => state.removeNotification);

  useEffect(() => {
    if (notification.duration !== Infinity) {
      const timer = setTimeout(() => {
        removeNotification(notification.id);
      }, notification.duration);
      return () => clearTimeout(timer);
    }
  }, [notification, removeNotification]);

  const Icon = icons[notification.type] || icons.info;
  const bgStyle = bgColors[notification.type] || bgColors.info;

  return (
    <div className={`toast-card ${bgStyle}`}>
      <div className="toast-content">
        <div className="toast-icon-wrapper">{Icon}</div>
        <div className="toast-text">
          <p className="toast-title">{notification.title}</p>
          <p className="toast-message">{notification.message}</p>
        </div>
        <div className="toast-close">
          <button type="button" className="toast-close-btn" onClick={() => removeNotification(notification.id)}>
            <span className="sr-only">Close</span>
            <X aria-hidden="true" />
          </button>
        </div>
      </div>
    </div>
  );
};

export const NotificationContainer = () => {
  const notifications = useNotificationStore(state => state.notifications);
  const addNotification = useNotificationStore(state => state.addNotification);

  // Request browser notification permission on mount if not granted/denied
  useEffect(() => {
    if (Notification.permission === 'default') {
      Notification.requestPermission();
    }
  }, []);

  // Listen for WebSocket notifications
  useEffect(() => {
    const handleWsMessage = (e) => {
      const data = e.detail;
      if (data?.type === 'notification' && data?.payload) {
        addNotification(data.payload);
      }
    };
    window.addEventListener('ws-message', handleWsMessage);
    return () => window.removeEventListener('ws-message', handleWsMessage);
  }, [addNotification]);

  return (
    <div aria-live="assertive" className="toast-container">
      <div className="toast-list">
        {notifications.map((notification) => (
          <Toast key={notification.id} notification={notification} />
        ))}
      </div>
    </div>
  );
};
