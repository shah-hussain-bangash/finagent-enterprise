# Email/password sign-in

This feature is not enabled merely by copying source files. Verify the candidate
with isolated PostgreSQL and Redis before applying migration 0012, rebuilding the
API/web images and enabling the two settings documented in ADR-0030.

Google sign-in remains available. Password sign-in is an additional administrator-
provisioned login; it does not automatically link a Google email to an analyst.

After installing verified code and applying the migration, run the console in a
trusted administrator terminal with FINAGENT_MIGRATION_DATABASE_URL configured:

    python -m finagent.security.authentication.password_console

The console lists existing active human principals with tenant, display name and
assigned roles. Select the intended principal ID, enter the login email, choose
set-password, then type the new password twice at the hidden prompts. Do not paste
passwords into chat. Use a dedicated FinAgent password, not a Google password.

Select an existing ANALYST principal for analyst access. Linking a password to a
PLATFORM_OPERATOR principal preserves its operator permissions and does not expose
case content. New principals and role changes remain separate authorized tasks.
An analyst sees only assigned cases. Reviewer approvals continue to require the
existing independent-review and MFA controls; password login cannot satisfy MFA.

To reset a password or unlock an account, repeat set-password for that same
principal and email. To revoke password access, choose disable. Existing password
sessions become invalid because the credential version changes. Google login and
existing role assignments are unaffected. For a normal sign-out use the app's
Sign out button; this revokes the password token as well as its web session.

Self-service registration, reset-email delivery, changing passwords in the web UI
and credential account recovery are not implemented. Credentials and expired
session metadata have no automatic deletion policy yet. Do not represent this
development implementation as production approved.
