"""Phase 8A discovery API. No runtime authority or execution capability."""
from .planner import plan_directory
from .contracts import validate_plan


def plan_repository(repository_url: str, ref: str | None = None) -> dict:
    """Reuse the existing bounded GitHub snapshot acquisition, without VM launch."""
    from projectiq_executioner.orchestration.adapters import pinned_checkout
    from projectiq_executioner.orchestration.contracts import source_matches_url, validate_request
    request=validate_request({'submission_id':'PHASE8A-PLAN','repository_url':repository_url,'ref':ref,'mode':'STATIC','max_vm_launches':0})
    repository_url,ref=request['repository_url'],request['ref']
    with pinned_checkout(repository_url, ref) as (source, checkout):
        if not source_matches_url(source, repository_url):
            raise ValueError('REPOSITORY_IDENTITY_MISMATCH')
        return plan_directory(checkout, source=source)

__all__=['plan_directory','plan_repository','validate_plan']
