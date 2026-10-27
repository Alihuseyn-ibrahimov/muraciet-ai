"""Workflow xətaları. Mesajlar Azərbaycan dilindədir və istifadəçiyə birbaşa göstərilə bilər."""


class WorkflowError(Exception):
    """İstifadəçiyə göstərilə bilən workflow xətası."""


class NotFound(WorkflowError):
    pass


class Forbidden(WorkflowError):
    pass


class InvalidTransition(WorkflowError):
    pass


class ValidationFailed(WorkflowError):
    pass


class StaleAppeal(WorkflowError):
    """Düymə köhnəlib: müraciət bu arada başqası tərəfindən dəyişdirilib.

    `current` ORM obyekti deyil, sadə snapshot-dur: tranzaksiya geri qaytarılanda ORM obyekti
    etibarsız olur, kart isə cari vəziyyətlə yenilənməlidir ("Artıq Rəşad H.-a təyin olunub").
    """

    def __init__(self, message: str, current: dict | None = None):
        super().__init__(message)
        self.current = current or {}
