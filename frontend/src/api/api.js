// API configuration and client for SmartSell3
const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

class ApiClient {
    constructor() {
        this.baseURL = API_BASE_URL;
    }

    async request(endpoint, options = {}) {
        const url = `${this.baseURL}${endpoint}`;
        const config = {
            headers: {
                'Content-Type': 'application/json',
                ...options.headers,
            },
            ...options,
        };

        if (config.body && typeof config.body === 'object') {
            config.body = JSON.stringify(config.body);
        }

        try {
            const response = await fetch(url, config);

            if (!response.ok) {
                const errorData = await response.json().catch(() => ({}));
                throw new Error(errorData.detail || `HTTP error! status: ${response.status}`);
            }

            const contentType = response.headers.get('content-type');
            if (contentType && contentType.includes('application/json')) {
                return await response.json();
            }

            return await response.text();
        } catch (error) {
            console.error('API request failed:', error);
            throw error;
        }
    }

    // Campaign endpoints
    async getCampaigns(skip = 0, limit = 100) {
        return this.request(`/api/v1/campaigns/?skip=${skip}&limit=${limit}`);
    }

    async getCampaign(campaignId) {
        return this.request(`/api/v1/campaigns/${campaignId}`);
    }

    async createCampaign(campaignData) {
        return this.request('/api/v1/campaigns/', {
            method: 'POST',
            body: campaignData,
        });
    }

    async updateCampaign(campaignId, campaignData) {
        return this.request(`/api/v1/campaigns/${campaignId}`, {
            method: 'PUT',
            body: campaignData,
        });
    }

    async deleteCampaign(campaignId) {
        return this.request(`/api/v1/campaigns/${campaignId}`, {
            method: 'DELETE',
        });
    }

    // Health check
    async healthCheck() {
        return this.request('/health');
    }
}

// Create and export singleton instance
const apiClient = new ApiClient();
export default apiClient;

// Export individual methods for convenience
export const {
    getCampaigns,
    getCampaign,
    createCampaign,
    updateCampaign,
    deleteCampaign,
    healthCheck
} = apiClient;
