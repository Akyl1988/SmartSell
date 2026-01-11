# Registration with Draft Company Tenant Creation

## Overview

The user registration endpoint (`POST /api/v1/auth/register`) now automatically creates a draft **Company tenant** and binds it to the newly registered user. This ensures that the companies table is never empty after registration and provides a multi-tenant foundation for each user.

## Implementation Details

### What Changed

1. **Modified**: [app/api/v1/auth.py](../app/api/v1/auth.py) - `register()` handler
2. **Added**: Company import
3. **Updated**: [tests/app/test_auth.py](../tests/app/test_auth.py) - Added regression tests

### Registration Flow

When a user registers via `/api/v1/auth/register`:

1. **Validate uniqueness** - Check phone/email uniqueness
2. **Create Company** (NEW):
   - Set name to `user_data.company_name` if provided
   - Otherwise use `f"Draft {normalized_phone}"` (e.g., `"Draft 77001234567"`)
   - Set `is_active=True` (active by default)
   - Set `subscription_plan="start"` (starter plan)
3. **Create User**:
   - Bind user to company via `user.company_id = company.id`
   - Set other fields as before (phone, email, password, etc.)
4. **Link Ownership**:
   - Set `company.owner_id = user.id` (user owns their draft company)
5. **Commit** - Single transaction ensures atomicity
6. **Issue Tokens** - Return access/refresh tokens as before

### Single Transaction Guarantee

All database operations use a single AsyncSession transaction:

```python
company = Company(...)
db.add(company)
await db.flush()  # Get company.id without committing

user = User(..., company_id=company.id)
db.add(user)
await db.flush()  # Get user.id without committing

company.owner_id = user.id
await db.commit()  # Single atomic commit
```

This ensures either both objects are created or neither is (no partial state).

## Request/Response Contract

### Request Body (Unchanged)

```json
{
  "phone": "+77001234567",
  "password": "securepassword123",
  "email": "user@example.com",  // optional
  "full_name": "John Doe",  // optional
  "company_name": "My Store",  // optional - if not provided, uses "Draft {phone}"
  "bin_iin": "123456789012"  // optional
}
```

### Response (Unchanged)

```json
{
  "access_token": "eyJ0eXAiOiJKV1QiLCJhbGc...",
  "refresh_token": "eyJ0eXAiOiJKV1QiLCJhbGc...",
  "token_type": "bearer",
  "expires_in": 3600
}
```

Response contract remains **backward compatible** — no breaking changes.

## Database State After Registration

After successful registration, the database contains:

```
users table:
  id: 1
  phone: "77001234567"
  email: "user@example.com"
  company_id: 5  ← Points to the created company
  is_active: true
  is_verified: false

companies table:
  id: 5
  name: "My Store"  (or "Draft 77001234567" if company_name not provided)
  owner_id: 1  ← Points back to the user
  is_active: true
  subscription_plan: "start"
  created_at: <timestamp>
```

## Company Name Logic

| Input | Result |
|-------|--------|
| `company_name: "My Store"` | Company name = `"My Store"` |
| `company_name: null` | Company name = `"Draft 77001234567"` |
| `company_name: ""` (empty) | Company name = `"Draft 77001234567"` |
| `company_name: "  "` (whitespace) | Company name = `"Draft 77001234567"` |

## Regression Tests

Two new regression tests verify the implementation:

### Test 1: `test_register_creates_draft_company_tenant`

- Registers user with explicit `company_name: "My Test Store"`
- Verifies:
  - Exactly 1 company created with name "My Test Store"
  - `user.company_id` points to that company
  - `company.owner_id == user.id`
  - `company.is_active == true`
  - `company.subscription_plan == "start"`

### Test 2: `test_register_creates_company_with_default_name`

- Registers user with no `company_name` provided
- Verifies:
  - Company created with name `"Draft 77008765432"` (normalized phone)
  - All relationships and defaults correct

**Test Status**: ✅ Both tests pass (15/15 auth tests pass)

## Backward Compatibility

- ✅ No changes to request/response contracts
- ✅ No changes to response status codes
- ✅ No changes to token format or behavior
- ✅ Existing login/auth flows unaffected
- ✅ All 15 existing auth tests still pass

## Migration Considerations

### For Existing Users

This change only affects **new registrations**. Existing users are unaffected:
- Users without companies will still work
- Users with companies will keep their existing company assignments
- No data migration required

### For API Clients

- API clients continue to work without modification
- Response structure unchanged
- Optional `company_name` field is now more useful but remains optional

## Usage Example

```bash
# Register with custom company name
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "phone": "+77001234567",
    "password": "securepassword123",
    "company_name": "My Business"
  }'

# Response includes tokens (company created automatically)
{
  "access_token": "...",
  "refresh_token": "...",
  "token_type": "bearer",
  "expires_in": 3600
}

# User can then query their company:
SELECT * FROM companies WHERE owner_id = 1;
```

## Files Modified

1. **[app/api/v1/auth.py](../app/api/v1/auth.py)**
   - Line 59: Added `from app.models.company import Company`
   - Lines 278-301: Modified `register()` handler to create Company tenant

2. **[tests/app/test_auth.py](../tests/app/test_auth.py)**
   - Line 6: Added `from sqlalchemy import select` import
   - Lines 41-107: Added two new regression tests

## Test Results

```
tests/app/test_auth.py::TestAuth::test_register_user PASSED
tests/app/test_auth.py::TestAuth::test_register_creates_draft_company_tenant PASSED
tests/app/test_auth.py::TestAuth::test_register_creates_company_with_default_name PASSED
tests/app/test_auth.py::TestAuth::test_register_duplicate_phone PASSED
...
============== 15 passed in 17.04s ==============
```

## Code Quality

- ✅ Ruff format: 2 files reformatted
- ✅ Ruff check: No issues
- ✅ All existing tests pass
- ✅ New regression tests added
- ✅ No breaking changes

## Future Enhancements

Possible future improvements:

1. **Subscription tier selection**: Allow users to select subscription plan during registration
2. **Company customization**: Accept additional company fields (address, email, phone, etc.)
3. **Multi-company registration**: Allow users to create multiple companies during registration
4. **Audit trail**: Log company creation as audit event
5. **Email verification**: Send welcome email with company details
