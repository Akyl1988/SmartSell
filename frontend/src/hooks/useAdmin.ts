import { useMemo } from 'react'
import {
  createAdminInvite,
  extendSubscription,
  getCompanies,
  getCompanyDetail,
  getPlatformSummary,
  getSubscriptionStores,
  grantKaspiTrial,
  runCampaignsCleanup,
  runCampaignsTask,
  runRepricingTask,
  runSubscriptionRenew,
  setSubscriptionPlan,
} from '../api/admin'
import { useAuth } from './useAuth'

export function useAdmin() {
  const { isPlatformAdmin } = useAuth()

  return useMemo(
    () => ({
      isPlatformAdmin,
      getPlatformSummary,
      getCompanies,
      getCompanyDetail,
      getSubscriptionStores,
      createAdminInvite,
      setSubscriptionPlan,
      extendSubscription,
      grantKaspiTrial,
      runSubscriptionRenew,
      runCampaignsTask,
      runCampaignsCleanup,
      runRepricingTask,
    }),
    [isPlatformAdmin]
  )
}
