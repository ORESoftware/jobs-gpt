"""Do not forward API secrets or local CDP requests through redirects."""

from urllib.request import HTTPRedirectHandler, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def open_no_redirect(request, timeout=30):
    return build_opener(NoRedirect).open(request, timeout=timeout)
