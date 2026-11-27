/** Group already-recorded reviewed CRUD observations without creating new test outcomes. */
export const CRUD_GROUPS = [
  {label:'Create',keys:['before_count','create_http','create_success','created_db_count','created_db_fields_match','id_is_valid']},
  {label:'Read',keys:['read_http','read_matches']},
  {label:'Update',keys:['update_http','update_success','read_updated_http','updated_db_count','updated_db_fields_match','updated_matches']},
  {label:'Delete and verify removal',keys:['delete_http','delete_success','read_deleted_http','deleted_db_count','deleted_matches']},
];
export function reviewedCrud(o: unknown) {
  if (!o || typeof o !== 'object' || Array.isArray(o)) return [];
  const row=o as Record<string,unknown>;
  if (row.suite!=='crud') return [];
  const values={...row,...(row.suite_metrics && typeof row.suite_metrics==='object'&&!Array.isArray(row.suite_metrics)?row.suite_metrics:{})};
  return CRUD_GROUPS.map(g=>({label:g.label,metrics:g.keys.filter(k=>typeof values[k]==='boolean'||typeof values[k]==='number').map(k=>({key:k,value:values[k] as boolean|number}))})).filter(g=>g.metrics.length);
}
