import React from 'react';
import { ScrollViewStyleReset, useServerDocumentContext } from 'expo-router/html';

export default function Root({ children }: { children: React.ReactNode }) {
  const { htmlAttributes, bodyAttributes, headNodes, bodyNodes } = useServerDocumentContext();

  return (
    <html lang="en" {...htmlAttributes}>
      <head>
        <meta charSet="utf-8" />
        <meta httpEquiv="X-UA-Compatible" content="IE=edge" />
        <meta
          name="viewport"
          content="width=device-width, initial-scale=1, shrink-to-fit=no, viewport-fit=cover"
        />
        <title>Grocery Management</title>
        <meta name="theme-color" content="#1B6B4A" />
        <meta name="application-name" content="Grocery Management" />
        <meta name="apple-mobile-web-app-capable" content="yes" />
        <meta name="apple-mobile-web-app-status-bar-style" content="default" />
        <meta name="apple-mobile-web-app-title" content="Grocery" />
        <link rel="manifest" href="/manifest.webmanifest" />
        <link rel="icon" href="/favicon.ico" />
        <link rel="apple-touch-icon" sizes="180x180" href="/icons/apple-touch-icon.png" />
        <style
          id="gm-pwa-base"
          dangerouslySetInnerHTML={{
            __html: `
              html, body, #root {
                height: 100%;
                min-height: 100dvh;
                width: 100%;
                margin: 0;
              }
              html, body {
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
                background-color: #F8FAF7;
                color: #111827;
              }
              #root {
                display: flex;
                flex-direction: column;
              }
              @keyframes gm-spin {
                from { transform: rotate(0deg); }
                to { transform: rotate(360deg); }
              }
              .gm-spinner {
                width: 40px;
                height: 40px;
                border-radius: 9999px;
                border: 3.5px solid #D1E7DD;
                border-top-color: #1B6B4A;
                box-sizing: border-box;
                animation: gm-spin 0.85s linear infinite;
                will-change: transform;
              }
              @media (prefers-reduced-motion: reduce) {
                .gm-spinner {
                  animation: none;
                  border-right-color: #1B6B4A;
                }
              }
            `,
          }}
        />
        <ScrollViewStyleReset />
        {headNodes}
      </head>
      <body {...bodyAttributes}>
        {children}
        {bodyNodes}
      </body>
    </html>
  );
}
