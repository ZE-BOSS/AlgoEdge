import React, { useEffect } from 'react';
import { useNotificationStore } from '../store';
import { X, CheckCircle, AlertTriangle, Info, XCircle } from 'lucide-react';

const icons = {
  success: <CheckCircle className="w-5 h-5 text-green-400" />,
  error: <XCircle className="w-5 h-5 text-red-400" />,
  warning: <AlertTriangle className="w-5 h-5 text-yellow-400" />,
  info: <Info className="w-5 h-5 text-blue-400" />,
};

const bgColors = {
  success: 'bg-green-500/10 border-green-500/20',
  error: 'bg-red-500/10 border-red-500/20',
  warning: 'bg-yellow-500/10 border-yellow-500/20',
  info: 'bg-blue-500/10 border-blue-500/20',
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
    <div className={`pointer-events-auto w-full max-w-sm overflow-hidden rounded-lg border shadow-lg ring-1 ring-black ring-opacity-5 transition-all ${bgStyle} backdrop-blur-md`}>
      <div className="p-4">
        <div className="flex items-start">
          <div className="flex-shrink-0 pt-0.5">{Icon}</div>
          <div className="ml-3 w-0 flex-1 pt-0.5">
            <p className="text-sm font-medium text-white">{notification.title}</p>
            <p className="mt-1 text-sm text-gray-300">{notification.message}</p>
          </div>
          <div className="ml-4 flex flex-shrink-0">
            <button
              type="button"
              className="inline-flex rounded-md bg-transparent text-gray-400 hover:text-white focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2"
              onClick={() => removeNotification(notification.id)}
            >
              <span className="sr-only">Close</span>
              <X className="h-5 w-5" aria-hidden="true" />
            </button>
          </div>
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
    <div
      aria-live="assertive"
      className="pointer-events-none fixed inset-0 flex px-4 py-6 sm:items-start sm:p-6 z-50 mt-16"
    >
      <div className="flex w-full flex-col items-center space-y-4 sm:items-end">
        {notifications.map((notification) => (
          <Toast key={notification.id} notification={notification} />
        ))}
      </div>
    </div>
  );
};
