export type ProvenanceType =
  | 'OBSERVED'
  | 'DERIVED'
  | 'INFERRED'
  | 'UNAVAILABLE'
  | 'AMBIGUOUS';

export type ExtractModeType = 'experimental' | 'existing' | 'compare';

export interface FieldEnvelope<T> {
  value: T | null;
  provenance: ProvenanceType;
  source_text_or_evidence_ref?: string | null;
  confidence: number;
  review_status: 'ACCEPTED' | 'NEEDS_REVIEW' | 'REJECTED';
}

export interface ExperimentalGstBreakdown {
  hsn: string | null;
  taxable_value: number | null;
  cgst_rate: number | null;
  cgst_amount: number | null;
  sgst_rate: number | null;
  sgst_amount: number | null;
  igst_rate: number | null;
  igst_amount: number | null;
  cess_rate: number | null;
  cess_amount: number | null;
  observed_tax_total: number | null;
  derived_tax_total: number | null;
  tax_provenance: ProvenanceType;
}

export interface ExperimentalItem {
  line_index: number;
  product_name: FieldEnvelope<string>;
  quantity: FieldEnvelope<number>;
  unit_of_measure: FieldEnvelope<string>;
  package_size: FieldEnvelope<string>;
  unit_price: FieldEnvelope<number>;
  mrp: FieldEnvelope<number>;
  selling_price: FieldEnvelope<number>;
  line_total: FieldEnvelope<number>;
  item_discount: FieldEnvelope<number>;
  gst: ExperimentalGstBreakdown;
  price_presentation: string;
}

export interface ExperimentalFee {
  fee_type: string;
  original_amount: number | null;
  actual_amount: number | null;
  discount_or_waiver: number | null;
  charge_status: 'CHARGED' | 'WAIVED' | 'DISCOUNTED' | 'UNKNOWN';
  provenance: ProvenanceType;
}

export interface ExperimentalExtractionResult {
  status: 'completed' | 'failed';
  provider: string;
  model: string;
  latency_ms: number;
  token_usage: Record<string, unknown>;
  raw_ocr_summary: Record<string, unknown>;
  merchant_evidence: {
    observed_merchant: string | null;
    inferred_merchant: string | null;
    inference_evidence: string | null;
    confidence: number;
    unavailable_merchant: boolean;
    provenance: ProvenanceType;
  };
  date_evidence: {
    printed_date: string | null;
    parsed_date: string | null;
    assumed_convention: string;
    confidence: number;
    ambiguity_status: 'UNAMBIGUOUS' | 'AMBIGUOUS_DAY_MONTH_SWAP' | 'UNPARSEABLE';
    provenance: ProvenanceType;
  };
  items: ExperimentalItem[];
  fees: ExperimentalFee[];
  subtotal_before_tax: FieldEnvelope<number>;
  subtotal_after_tax: FieldEnvelope<number>;
  grand_total: FieldEnvelope<number>;
  warnings: string[];
  error: string | null;
}

export interface ExistingOcrSnapshot {
  status: 'completed' | 'failed';
  adapter_used: string | null;
  latency_ms: number;
  merchant_name: string | null;
  receipt_date: string | null;
  subtotal_rupees: number | null;
  tax_rupees: number | null;
  grand_total_rupees: number | null;
  items: Array<{
    name: string;
    qty: number;
    unit: string;
    unit_price_rupees: number | null;
    line_total_rupees: number | null;
  }>;
  warnings: string[];
  error: string | null;
}

export interface ExperimentalJobOut {
  job_id: string;
  household_id: string;
  mode: ExtractModeType;
  filename: string;
  created_at: string;
  experimental_result: ExperimentalExtractionResult | null;
  existing_result: ExistingOcrSnapshot | null;
  ground_truth: Record<string, unknown> | null;
  inventory_mutated: boolean;
}
