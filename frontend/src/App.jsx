import React, { useState, useEffect } from 'react'
import apiClient from './api/api.js'

function App() {
  const [campaigns, setCampaigns] = useState([])
  const [health, setHealth] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    loadData()
  }, [])

  const loadData = async () => {
    try {
      setLoading(true)

      // Test health endpoint
      const healthData = await apiClient.healthCheck()
      setHealth(healthData)

      // Load campaigns
      const campaignsData = await apiClient.getCampaigns()
      setCampaigns(campaignsData)

    } catch (err) {
      console.error('Error loading data:', err)
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  const createSampleCampaign = async () => {
    try {
      const campaignData = {
        title: `Sample Campaign ${Date.now()}`,
        description: 'This is a sample campaign created from the frontend',
        messages: [
          {
            recipient: 'test@example.com',
            content: 'Hello! This is a test message from SmartSell3.',
            status: 'pending'
          }
        ]
      }

      await apiClient.createCampaign(campaignData)
      await loadData() // Reload campaigns

    } catch (err) {
      console.error('Error creating campaign:', err)
      setError(err.message)
    }
  }

  if (loading) {
    return <div style={{ padding: '20px' }}>Loading...</div>
  }

  return (
    <div style={{ padding: '20px', fontFamily: 'Arial, sans-serif' }}>
      <h1>SmartSell3 Campaign Manager</h1>

      {error && (
        <div style={{
          background: '#f8d7da',
          color: '#721c24',
          padding: '10px',
          borderRadius: '4px',
          marginBottom: '20px'
        }}>
          Error: {error}
        </div>
      )}

      <div style={{ marginBottom: '20px' }}>
        <h2>System Status</h2>
        <p>API Health: <strong>{health?.status || 'Unknown'}</strong></p>
        <p>API URL: <strong>{import.meta.env.VITE_API_URL || 'http://localhost:8000'}</strong></p>
      </div>

      <div style={{ marginBottom: '20px' }}>
        <h2>Campaigns ({campaigns.length})</h2>
        <button
          onClick={createSampleCampaign}
          style={{
            background: '#007bff',
            color: 'white',
            border: 'none',
            padding: '10px 20px',
            borderRadius: '4px',
            cursor: 'pointer',
            marginBottom: '15px'
          }}
        >
          Create Sample Campaign
        </button>

        {campaigns.length === 0 ? (
          <p>No campaigns found. Create one using the button above!</p>
        ) : (
          <div>
            {campaigns.map(campaign => (
              <div key={campaign.id} style={{
                border: '1px solid #ddd',
                borderRadius: '4px',
                padding: '15px',
                marginBottom: '10px',
                background: '#f9f9f9'
              }}>
                <h3>{campaign.title}</h3>
                <p><strong>Status:</strong> {campaign.status}</p>
                <p><strong>Description:</strong> {campaign.description || 'No description'}</p>
                <p><strong>Messages:</strong> {campaign.messages?.length || 0}</p>
                <p><strong>Created:</strong> {new Date(campaign.created_at).toLocaleString()}</p>
                {campaign.scheduled_at && (
                  <p><strong>Scheduled:</strong> {new Date(campaign.scheduled_at).toLocaleString()}</p>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

export default App
