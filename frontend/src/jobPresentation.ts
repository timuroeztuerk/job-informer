import type { Job } from "./types";

export const jobLocation = (job: Job): string => {
  if ((job.posting_count ?? 1) < 2 || !job.postings?.length) return job.location;
  const cities = [...new Set(job.postings.map(({ location }) => location.split(",")[0].trim()))];
  return cities.length > 2 ? `${cities.slice(0, 2).join(", ")} +${cities.length - 2} more locations` : cities.join(", ");
};
