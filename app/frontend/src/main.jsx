// v1.2.0 theme bump — editing this entry module makes Vite force a full
// browser reload on all connected tabs (remote refresh)
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App.jsx'

const root = document.getElementById('root')
root.style.height = '100%'
createRoot(root).render(<StrictMode><App /></StrictMode>)
