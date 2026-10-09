import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, qs } from "./client";
import type {
  Application, Draft, Facets, Insights, IntegrationStatus, JobDetail, JobListItem, KnowledgeDoc, Overview, Page,
  PipelineHealth, ProfileData, ProfileRecord, Resume,
} from "./types";
import type { JobQuery } from "../lib/filters";

export const keys = {
  overview: ["overview"] as const,
  jobs: (q: JobQuery) => ["jobs", q] as const,
  job: (id: number) => ["job", id] as const,
  drafts: (id: number) => ["drafts", id] as const,
  facets: ["facets"] as const,
  applications: ["applications"] as const,
  insights: ["insights"] as const,
  profile: ["profile"] as const,
  resumes: ["resumes"] as const,
  integrations: ["integrations"] as const,
  knowledge: ["knowledge"] as const,
  pipeline: ["pipeline"] as const,
  feedbackCategories: ["feedback-categories"] as const,
};

export const useOverview = () => useQuery({ queryKey: keys.overview, queryFn: () => api.get<Overview>("/api/v2/overview") });

export const useJobs = (q: JobQuery) =>
  useQuery({
    queryKey: keys.jobs(q),
    queryFn: () => api.get<Page<JobListItem>>(`/api/v2/jobs${qs({ ...q })}`),
    placeholderData: keepPreviousData, // keep the list on screen while the next page/filter loads
  });

export const useJob = (id: number | null) =>
  useQuery({ queryKey: keys.job(id ?? 0), queryFn: () => api.get<JobDetail>(`/api/v2/jobs/${id}`), enabled: id !== null });

export const useFacets = () => useQuery({ queryKey: keys.facets, queryFn: () => api.get<Facets>("/api/v2/jobs/facets"), staleTime: 5 * 60_000 });

export const useFeedbackCategories = () =>
  useQuery({
    queryKey: keys.feedbackCategories,
    queryFn: () => api.get<{ value: string; label: string; teaches: boolean }[]>("/api/v2/feedback/categories"),
    staleTime: Infinity,
  });

/** After anything that changes a job's state, refresh the views derived from it. */
function useInvalidateJob() {
  const qc = useQueryClient();
  return (jobId?: number) => {
    if (jobId) qc.invalidateQueries({ queryKey: keys.job(jobId) });
    qc.invalidateQueries({ queryKey: ["jobs"] });
    qc.invalidateQueries({ queryKey: keys.overview });
    qc.invalidateQueries({ queryKey: keys.applications });
  };
}

export type ApplicationPatch = Partial<Pick<Application, "status" | "saved" | "notes" | "resume_id" | "application_url" |
  "recruiter_name" | "recruiter_contact" | "next_follow_up_at" | "final_draft_id">> & { note?: string };

export function useUpdateApplication(jobId: number) {
  const qc = useQueryClient();
  const invalidate = useInvalidateJob();
  return useMutation({
    mutationFn: (patch: ApplicationPatch) => api.put<JobDetail>(`/api/v2/jobs/${jobId}/application`, patch),
    onSuccess: (detail) => {
      qc.setQueryData(keys.job(jobId), (old: JobDetail | undefined) => (old ? { ...old, ...detail, thresholds: old.thresholds } : old));
      invalidate(jobId);
      qc.invalidateQueries({ queryKey: keys.insights });
    },
  });
}

export function useSubmitFeedback(jobId: number) {
  const qc = useQueryClient();
  const invalidate = useInvalidateJob();
  return useMutation({
    mutationFn: (body: { category: string; note?: string }) =>
      api.post<{ newly_learned: { dimension: string; value: string; explanation: string }[]; priority_now: number | null }>(
        `/api/v2/jobs/${jobId}/feedback`, body),
    onSuccess: () => {
      invalidate(jobId);
      qc.invalidateQueries({ queryKey: keys.insights });
    },
  });
}

export function useVerifyJob(jobId: number) {
  const invalidate = useInvalidateJob();
  return useMutation({
    mutationFn: () => api.post<{ result: string; detail: string | null }>(`/api/v2/jobs/${jobId}/verify`),
    onSettled: () => invalidate(jobId),
  });
}

export const useDrafts = (jobId: number) =>
  useQuery({ queryKey: keys.drafts(jobId), queryFn: () => api.get<{ items: Draft[]; llm_configured: boolean }>(`/api/v2/jobs/${jobId}/drafts`) });

export function useDraftMutations(jobId: number) {
  const qc = useQueryClient();
  const invalidate = useInvalidateJob();
  const refresh = () => {
    qc.invalidateQueries({ queryKey: keys.drafts(jobId) });
    invalidate(jobId);
  };
  return {
    generate: useMutation({
      mutationFn: (body: { resume_id?: number | null; include_cover_letter: boolean; questions: string[] }) =>
        api.post<Draft>(`/api/v2/jobs/${jobId}/drafts/generate`, body),
      onSuccess: refresh,
    }),
    save: useMutation({
      mutationFn: (body: { subject: string | null; body: string; cover_letter: string | null; parent_id: number | null }) =>
        api.post<Draft>(`/api/v2/jobs/${jobId}/drafts`, body),
      onSuccess: refresh,
    }),
    gmail: useMutation({
      mutationFn: ({ draftId, to }: { draftId: number; to?: string }) => api.post<Draft>(`/api/v2/drafts/${draftId}/gmail`, { to: to || null }),
      onSettled: refresh,
    }),
    refreshGmail: useMutation({
      mutationFn: (draftId: number) => api.post<Draft>(`/api/v2/drafts/${draftId}/gmail/refresh`),
      onSettled: refresh,
    }),
  };
}

export function useTaskMutations(jobId: number) {
  const invalidate = useInvalidateJob();
  const done = () => invalidate(jobId);
  return {
    create: useMutation({
      mutationFn: (body: { kind: string; title: string; due_at: string | null; notes?: string | null }) => api.post(`/api/v2/jobs/${jobId}/tasks`, body),
      onSuccess: done,
    }),
    update: useMutation({
      mutationFn: ({ id, ...body }: { id: number; done?: boolean; title?: string; due_at?: string | null }) => api.patch(`/api/v2/tasks/${id}`, body),
      onSuccess: done,
    }),
    remove: useMutation({ mutationFn: (id: number) => api.del(`/api/v2/tasks/${id}`), onSuccess: done }),
  };
}

export const useApplications = () =>
  useQuery({
    queryKey: keys.applications,
    queryFn: () => api.get<{ items: (Application & { job_id: number; title: string; company: string; location: string | null; fit_score: number | null; priority_score: number | null; verification_status: string })[]; statuses: string[] }>("/api/v2/applications"),
  });

export const useInsights = () => useQuery({ queryKey: keys.insights, queryFn: () => api.get<Insights>("/api/v2/insights") });

export function useInsightMutations() {
  const qc = useQueryClient();
  const refresh = () => {
    qc.invalidateQueries({ queryKey: keys.insights });
    qc.invalidateQueries({ queryKey: ["jobs"] });
    qc.invalidateQueries({ queryKey: ["job"] });
  };
  return {
    toggle: useMutation({ mutationFn: ({ id, disabled }: { id: number; disabled: boolean }) => api.patch(`/api/v2/insights/preferences/${id}`, { disabled }), onSuccess: refresh }),
    reset: useMutation({ mutationFn: () => api.post("/api/v2/insights/reset"), onSuccess: refresh }),
    deleteFeedback: useMutation({ mutationFn: (id: number) => api.del(`/api/v2/feedback/${id}`), onSuccess: refresh }),
  };
}

export const useProfile = () => useQuery({ queryKey: keys.profile, queryFn: () => api.get<ProfileRecord>("/api/v2/profile") });

export function useSaveProfile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { data: ProfileData; additional_info: string; version: number }) =>
      api.put<ProfileRecord & { rescored_jobs: number }>("/api/v2/profile", body),
    onSuccess: (saved) => {
      qc.setQueryData(keys.profile, saved);
      qc.invalidateQueries({ queryKey: ["jobs"] });
      qc.invalidateQueries({ queryKey: ["job"] });
      qc.invalidateQueries({ queryKey: keys.overview });
    },
  });
}

export const useResumes = () =>
  useQuery({ queryKey: keys.resumes, queryFn: () => api.get<{ items: Resume[]; max_bytes: number; llm_configured: boolean }>("/api/v2/resumes") });

export function useResumeMutations() {
  const qc = useQueryClient();
  const refresh = () => qc.invalidateQueries({ queryKey: keys.resumes });
  return {
    upload: useMutation({ mutationFn: (form: FormData) => api.upload<Resume>("/api/v2/resumes", form), onSuccess: refresh }),
    update: useMutation({ mutationFn: ({ id, ...body }: { id: number; display_name?: string; purpose?: string | null; is_default?: boolean }) => api.patch<Resume>(`/api/v2/resumes/${id}`, body), onSuccess: refresh }),
    remove: useMutation({ mutationFn: (id: number) => api.del(`/api/v2/resumes/${id}`), onSuccess: refresh }),
    extract: useMutation({ mutationFn: (id: number) => api.post<Resume>(`/api/v2/resumes/${id}/extract`), onSuccess: refresh }),
    apply: useMutation({
      mutationFn: ({ id, sections }: { id: number; sections: string[] }) => api.post<ProfileRecord>(`/api/v2/resumes/${id}/apply`, { sections }),
      onSuccess: (saved) => {
        qc.setQueryData(keys.profile, saved);
        refresh();
      },
    }),
    refreshDrive: useMutation({ mutationFn: (id: number) => api.post<{ changed: boolean }>(`/api/v2/resumes/${id}/refresh`), onSuccess: refresh }),
  };
}

export const useIntegrations = () => useQuery({ queryKey: keys.integrations, queryFn: () => api.get<IntegrationStatus>("/api/v2/integrations") });
export const useKnowledge = () => useQuery({ queryKey: keys.knowledge, queryFn: () => api.get<{ items: KnowledgeDoc[] }>("/api/v2/knowledge") });

export function useIntegrationMutations() {
  const qc = useQueryClient();
  const refresh = () => {
    qc.invalidateQueries({ queryKey: keys.integrations });
    qc.invalidateQueries({ queryKey: keys.knowledge });
    qc.invalidateQueries({ queryKey: keys.resumes });
  };
  return {
    connect: useMutation({
      mutationFn: (service: "gmail" | "drive") => api.post<{ authorization_url: string }>(`/api/v2/integrations/google/${service}/connect`),
      onSuccess: ({ authorization_url }) => window.location.assign(authorization_url),
    }),
    disconnect: useMutation({
      mutationFn: ({ service, deleteImported }: { service: string; deleteImported?: boolean }) =>
        api.del(`/api/v2/integrations/google/${service}${deleteImported ? "?delete_imported=true" : ""}`),
      onSuccess: refresh,
    }),
    importDrive: useMutation({
      mutationFn: (body: { file_ids: string[]; kind: "resume" | "knowledge" }) =>
        api.post<{ results: { file_id: string; ok: boolean; error?: string }[] }>("/api/v2/integrations/drive/import", body),
      onSuccess: refresh,
    }),
    refreshDoc: useMutation({ mutationFn: (id: number) => api.post<{ changed: boolean }>(`/api/v2/knowledge/${id}/refresh`), onSuccess: refresh }),
    deleteDoc: useMutation({ mutationFn: (id: number) => api.del(`/api/v2/knowledge/${id}`), onSuccess: refresh }),
  };
}

export const usePipeline = () => useQuery({ queryKey: keys.pipeline, queryFn: () => api.get<PipelineHealth>("/api/v2/pipeline") });
