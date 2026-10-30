export type QuantityRow = {
  row_id: string; client_code: string; product_name: string; product_code: string | null;
  quantity: number; unit: string; pack_size: string | null; net_weight_kg: number | null;
  source: Record<string, unknown>;
  computed: { pack_count: number; net_weight_kg: number } | null; issue: string | null;
};

export type QuantityReview = {
  revision: string; items: QuantityRow[]; total: number; filtered_total?: number;
  issue_count: number; unknown_unit_count: number; has_more: boolean;
  template_issues?: string[]; changed_rows?: QuantityRow[]; changed_count?: number;
};
