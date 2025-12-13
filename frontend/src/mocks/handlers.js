import { http, HttpResponse } from 'msw'

// Демоданные
let campaigns = [
  { id: 1, name: 'Welcome campaign', title: 'Welcome campaign', status: 'DRAFT',  created_at: '2025-10-08T12:00:00Z' },
  { id: 2, name: 'Autumn promo',     title: 'Autumn promo',     status: 'ACTIVE', created_at: '2025-10-09T09:30:00Z' },
]

// ВАЖНО: используем шаблон '*/path', чтобы матчить и same-origin, и http://127.0.0.1:8000/...
export const handlers = [
  // health
  http.get('*/health', () =>
    HttpResponse.json({ status: 'healthy', version: 'mock-0.1.0' })
  ),

  // list
  http.get('*/api/v1/campaigns', ({ request }) => {
    const url = new URL(request.url)
    const skip  = Number(url.searchParams.get('skip')  ?? 0)
    const limit = Number(url.searchParams.get('limit') ?? campaigns.length)
    return HttpResponse.json(campaigns.slice(skip, skip + limit))
  }),

  // get one
  http.get('*/api/v1/campaigns/:id', ({ params }) => {
    const c = campaigns.find(x => String(x.id) === String(params.id))
    return c
      ? HttpResponse.json(c)
      : HttpResponse.json({ detail: 'not_found' }, { status: 404 })
  }),

  // create
  http.post('*/api/v1/campaigns', async ({ request }) => {
    const body = await request.json()
    const id = campaigns.length ? Math.max(...campaigns.map(c => c.id)) + 1 : 1
    const created = {
      id,
      status: 'DRAFT',
      created_at: new Date().toISOString(),
      ...body,
      title: body.title ?? body.name ?? `Untitled ${id}`,
      name:  body.name  ?? body.title ?? `Untitled ${id}`,
    }
    campaigns.unshift(created)
    return HttpResponse.json(created, { status: 201 })
  }),

  // update
  http.put('*/api/v1/campaigns/:id', async ({ params, request }) => {
    const i = campaigns.findIndex(x => String(x.id) === String(params.id))
    if (i < 0) return HttpResponse.json({ detail: 'not_found' }, { status: 404 })
    const patch = await request.json()
    campaigns[i] = { ...campaigns[i], ...patch }
    return HttpResponse.json(campaigns[i])
  }),

  // delete
  http.delete('*/api/v1/campaigns/:id', ({ params }) => {
    const i = campaigns.findIndex(x => String(x.id) === String(params.id))
    if (i < 0) return HttpResponse.json({ detail: 'not_found' }, { status: 404 })
    const [removed] = campaigns.splice(i, 1)
    return HttpResponse.json(removed)
  }),
]
