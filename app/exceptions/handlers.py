from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


# Exceções de domínio
class BadRequestException(Exception):
    """400 — Requisição inválida."""

    def __init__(self, detail: str = "Requisição inválida"):
        self.detail = detail


class UnauthorizedException(Exception):
    """401 — API Key ausente ou inválida."""

    def __init__(self, detail: str = "API Key ausente ou inválida"):
        self.detail = detail


class ForbiddenException(Exception):
    """403 — Recurso proibido para o solicitante."""

    def __init__(self, detail: str = "Acesso negado"):
        self.detail = detail


class NotFoundException(Exception):
    """404 — Recurso não encontrado."""

    def __init__(self, detail: str = "Recurso não encontrado"):
        self.detail = detail


class ConflictException(Exception):
    """409 — Conflito de estado (ex: lock já detido por outro serviço)."""

    def __init__(self, detail: str = "Conflito"):
        self.detail = detail


class UnprocessableEntityException(Exception):
    """422 — Transição ou payload inválido."""

    def __init__(self, detail: str = "Entidade não processável"):
        self.detail = detail


class InternalServerErrorException(Exception):
    """500 — Erro interno do servidor."""

    def __init__(self, detail: str = "Erro interno do servidor"):
        self.detail = detail


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _error_body(error: str, detail: str) -> dict:
    return {"error": error, "detail": detail, "rideUuid": None}


# ---------------------------------------------------------------------------
# Registro dos handlers
# ---------------------------------------------------------------------------

def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(BadRequestException)
    async def bad_request_handler(request: Request, exc: BadRequestException):
        return JSONResponse(
            status_code=400,
            content=_error_body("Bad Request", exc.detail),
        )

    @app.exception_handler(UnauthorizedException)
    async def unauthorized_handler(request: Request, exc: UnauthorizedException):
        return JSONResponse(
            status_code=401,
            content=_error_body("Unauthorized", exc.detail),
        )

    @app.exception_handler(ForbiddenException)
    async def forbidden_handler(request: Request, exc: ForbiddenException):
        return JSONResponse(
            status_code=403,
            content=_error_body("Forbidden", exc.detail),
        )

    @app.exception_handler(NotFoundException)
    async def not_found_handler(request: Request, exc: NotFoundException):
        return JSONResponse(
            status_code=404,
            content=_error_body("Not Found", exc.detail),
        )

    @app.exception_handler(ConflictException)
    async def conflict_handler(request: Request, exc: ConflictException):
        return JSONResponse(
            status_code=409,
            content=_error_body("Conflict", exc.detail),
        )

    @app.exception_handler(UnprocessableEntityException)
    async def unprocessable_handler(request: Request, exc: UnprocessableEntityException):
        return JSONResponse(
            status_code=422,
            content=_error_body("Unprocessable Entity", exc.detail),
        )

    @app.exception_handler(InternalServerErrorException)
    async def internal_error_handler(request: Request, exc: InternalServerErrorException):
        return JSONResponse(
            status_code=500,
            content=_error_body("Internal Server Error", exc.detail),
        )

    # Garante que HTTPExceptions levantadas pelo FastAPI/Starlette também
    # sigam o schema ErrorResponse do contrato.
    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        _status_labels = {
            400: "Bad Request",
            401: "Unauthorized",
            403: "Forbidden",
            404: "Not Found",
            409: "Conflict",
            422: "Unprocessable Entity",
            500: "Internal Server Error",
        }
        label = _status_labels.get(exc.status_code, "HTTP Error")
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(label, str(exc.detail)),
        )

    # Erros de validação do Pydantic (422)
    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        errors = "; ".join(
            f"{' -> '.join(str(loc) for loc in e['loc'])}: {e['msg']}"
            for e in exc.errors()
        )
        return JSONResponse(
            status_code=422,
            content=_error_body("Unprocessable Entity", errors),
        )

    # Catch-all para exceções não tratadas
    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        return JSONResponse(
            status_code=500,
            content=_error_body("Internal Server Error", "Erro interno do servidor"),
        )
