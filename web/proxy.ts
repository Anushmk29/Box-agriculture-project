import { NextRequest, NextResponse } from "next/server";

function hasUnexpiredSession(request: NextRequest): boolean {
  const token = request.cookies.get("access_token")?.value;
  if (!token) return false;

  try {
    const payloadSegment = token.split(".")[1];
    if (!payloadSegment) return false;
    const payload = JSON.parse(
      Buffer.from(payloadSegment, "base64url").toString("utf8"),
    ) as { exp?: number };
    return typeof payload.exp === "number" && payload.exp * 1000 > Date.now();
  } catch {
    return false;
  }
}

export function proxy(request: NextRequest) {
  const authenticated = hasUnexpiredSession(request);
  const loginUrl = new URL("/login", request.url);

  if (!authenticated && request.nextUrl.pathname !== "/login") {
    loginUrl.searchParams.set("next", request.nextUrl.pathname + request.nextUrl.search);
    return NextResponse.redirect(loginUrl);
  }
  // An unexpired cookie can refer to a deleted or revoked account.
  // Keep login reachable; only the backend can validate the session.
  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!backend|_next/static|_next/image|favicon.ico|.*\\.(?:png|jpg|svg|ico)$).*)"],
};
