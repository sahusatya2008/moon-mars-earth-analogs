export type BodyId = "earth" | "moon" | "mars";

export interface Planet {
  id: BodyId;
  name: string;
  radius_km: number;
  description: string;
  accent_color: string;
  display_order: number;
}

export interface Dataset {
  id: string;
  title: string;
  body_id: string;
  source_agency: string;
  mission: string | null;
  instrument: string | null;
  description: string;
  variable_names: string[];
  access_protocol: string;
  source_url: string;
  documentation_url: string | null;
  citation: string;
  license: string;
  license_url: string | null;
  spatial_resolution_m: number | null;
  temporal_resolution: string | null;
  temporal_coverage: string | null;
  processing_level: string | null;
  is_scoring_input: boolean;
  requires_credentials: boolean;
  status: string;
  status_detail: string | null;
  last_checked_at: string | null;
  last_success_at: string | null;
}

export interface Region {
  id: string;
  body_id: BodyId;
  name: string;
  kind: string;
  description: string | null;
  center_lon: number;
  center_lat: number;
  size_km: number;
  geometry: GeoJSONPolygon | null;
  source: string | null;
  source_url: string | null;
  citation: string | null;
  is_preset: boolean;
  metadata_json: Record<string, unknown>;
  created_at: string;
}

export interface GeoJSONPolygon {
  type: "Polygon";
  coordinates: number[][][];
}

export interface SearchConfig {
  bbox: [number, number, number, number];
  grid_mode: "latlon" | "h3";
  cell_size_deg: number;
  h3_resolution: number;
  screening_resolution_km: number;
  refine_top_n: number;
  max_candidates: number;
  cluster_radius_km: number;
  random_seed: number;
  exclude_polar_gt_deg: number;
}

export interface AnalysisConfig {
  footprint_km: number;
  analysis_resolution_m: number;
  dimensions: string[];
  weights: Record<string, number>;
  methods: string[];
  min_feature_coverage: number;
  missing_data_policy: string;
  temporal_start: string | null;
  temporal_end: string | null;
  search: SearchConfig;
}

export interface Analysis {
  id: string;
  title: string;
  reference_region_id: string;
  status: string;
  stage: string;
  progress: number;
  algorithm_version: string;
  feature_schema_version: string;
  config: AnalysisConfig;
  dataset_manifest: Record<string, unknown>;
  result_summary: ResultSummary | null;
  error_message: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
}

export interface ResultSummary {
  candidate_count: number;
  top_candidates: Array<{
    rank: number;
    candidate_id: string;
    display_name: string;
    lon: number;
    lat: number;
    overall_score: number;
    confidence: number;
    confidence_band: string;
    evidence_coverage: number;
  }>;
  confidence_bands: Record<string, number>;
  mean_confidence: number;
  method_agreement: {
    methods: Record<string, { mean: number | null; std: number | null; n: number }>;
    agreement: { mean_method_std: number | null; interpretation: string };
  };
  screening: { coarse_samples: number | null; refinement_sites: number | null; resolution_m: number | null };
  refinement: Record<string, unknown>;
  context_errors: string[];
  limitations: string[];
}

export interface DimensionScore {
  dimension: string;
  label: string;
  score: number | null;
  coverage: number;
  weight_configured: number;
  weight_used: number;
  features_used: number;
  features_missing: number;
  methods: Record<string, number | null>;
  features: FeatureDelta[];
  unavailable_reason: string | null;
}

export interface FeatureDelta {
  key: string;
  reference: number;
  candidate: number;
  delta: number | null;
  unit: string;
  similarity: number;
  tolerance: number;
  weight: number;
}

export interface Ranking {
  rank: number;
  candidate_id: string;
  display_name: string;
  name: string;
  country: string | null;
  admin_region: string | null;
  center_lon: number;
  center_lat: number;
  size_km: number;
  geometry: GeoJSONPolygon | null;
  overall_score: number;
  confidence: number;
  confidence_band: string;
  evidence_coverage: number;
  dimension_scores: Record<string, DimensionScore>;
  method_scores: Record<string, number | null>;
  missing_dimensions: Array<{ dimension: string; label: string; reason: string | null; coverage: number }>;
  strongest_dimensions: Array<{ dimension: string; label: string; score: number }>;
  weakest_dimensions: Array<{ dimension: string; label: string; score: number }>;
  limitations: string[];
  elevation_mean: number | null;
}

export interface AnalysisResults {
  analysis: Analysis;
  reference_region: Region;
  rankings: Ranking[];
  method_agreement: ResultSummary["method_agreement"];
  dataset_manifest: DatasetManifest;
  reproducibility: Record<string, unknown>;
  limitations: string[];
}

export interface DatasetManifest {
  reference?: Record<string, unknown>;
  search?: Record<string, unknown>;
  screening?: Record<string, unknown>;
  refinement?: Record<string, unknown>;
  dimensions?: Record<string, unknown>;
  similarity?: Record<string, unknown>;
  datasets?: Array<{
    dataset_id: string;
    role: string;
    source_url?: string;
    citation?: string;
    native_resolution_m?: number;
  }>;
  limitations?: string[];
  [key: string]: unknown;
}

export interface FeatureMeasurement {
  subject_type: string;
  subject_id: string;
  body_id: string;
  dimension: string;
  feature_key: string;
  value: number | null;
  unit: string;
  raw_value: number | null;
  normalized_value: number | null;
  uncertainty: number | null;
  quality_flag: string;
  valid_fraction: number | null;
  native_resolution_m: number | null;
  analysis_resolution_m: number | null;
  dataset_id: string | null;
  product_id: string | null;
  source_url: string | null;
  citation: string | null;
  processing_version: string | null;
  method: string | null;
  acquired_at: string | null;
  metadata_json: Record<string, unknown>;
}

export interface Evidence {
  id: number;
  dataset_id: string;
  title: string;
  source_url: string | null;
  citation: string | null;
  license: string | null;
  processing_level: string | null;
  spatial_resolution_m: number | null;
  acquisition_datetime: string | null;
  uncertainty: number | null;
  details: Record<string, unknown>;
}

export interface CandidateDetail {
  candidate: Ranking;
  reference_region: Region;
  reference_features: FeatureMeasurement[];
  candidate_features: FeatureMeasurement[];
  evidence: Evidence[];
  profile: {
    analysis_id: string;
    analysis_title: string;
    body_id: string;
    center_lon: number;
    center_lat: number;
    size_km: number;
    elevation_mean_m: number | null;
    coarse_screen_score: number | null;
    context: Record<string, unknown>;
    gazetteer: string | null;
    refinement_dataset: string | null;
    refinement_method: string | null;
    analysis_status: string;
    algorithm_version: string;
    feature_schema_version: string;
  };
}

export interface JobEvent {
  id: number;
  job_id: string;
  at: string;
  level: string;
  stage: string;
  progress: number;
  message: string;
  data: Record<string, unknown>;
}

export interface Report {
  id: string;
  analysis_id: string;
  format: "pdf" | "csv" | "geojson" | "json" | "manifest";
  title: string;
  status: string;
  size_bytes: number | null;
  checksum_sha256: string | null;
  error_message: string | null;
  created_at: string;
  completed_at: string | null;
}

export interface AssistantAnswer {
  answer: string;
  citations: Array<{
    dataset_id: string;
    title: string;
    source_url: string | null;
    citation: string | null;
    license: string | null;
  }>;
  provider: string;
  grounded: boolean;
  limitations: string[];
  analysis_id: string | null;
}

export interface DatasetAvailability {
  dataset_id: string;
  status: string;
  detail: string;
  checked_at: string;
  http_status: number | null;
  latency_ms: number | null;
  probe_url: string | null;
}

export interface TerrainSource {
  dataset_id: string;
  product_id: string;
  source_url: string;
  citation: string;
  license?: string;
  native_resolution_m: number | null;
  vertical_uncertainty_m: number | null;
  method: string;
}

export interface TerrainImagery {
  available: boolean;
  url: string;
  dataset_id: string;
  source: string;
  attribution: string;
  role: string;
}

export interface TerrainMesh {
  body_id: BodyId;
  center: { lon: number; lat: number };
  size_km: number;
  width: number;
  height: number;
  resolution_m: number;
  row_order: "north_to_south";
  col_order: "west_to_east";
  vertical_units: "m";
  valid_fraction: number;
  min_elevation_m: number;
  max_elevation_m: number;
  mean_elevation_m: number;
  heights: Array<Array<number | null>>;
  source: TerrainSource;
  imagery?: TerrainImagery;
  note: string;
  generated_at: string;
  cache_hit: boolean;
}

export interface Comparison {
  id: string;
  reference_region_id: string;
  candidate_ids: string[];
  config: Record<string, unknown>;
  results: {
    candidates: Record<
      string,
      {
        dimension_scores: Record<string, DimensionScore>;
        method_scores: Record<string, number | null>;
        confidence: number;
        evidence_coverage: number;
      }
    >;
    dimension_deltas: Record<string, { values: Record<string, number | null>; spread: number | null }>;
    note: string;
  } | null;
  status: string;
  created_at: string;
}
