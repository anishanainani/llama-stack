# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the terms described in the LICENSE file in
# the root directory of this source tree.

from typing import List, Protocol

from llama_models.schema_utils import webmethod

from pydantic import BaseModel

from llama_models.llama3_1.api.datatypes import *  # noqa: F403
from .datatypes import *  # noqa: F403
from llama_toolchain.dataset.api.datatypes import *  # noqa: F403
from llama_toolchain.common.training_types import *  # noqa: F403


class EvaluateTaskRequestCommon(BaseModel):
    job_uuid: str
    dataset: TrainEvalDataset

    checkpoint: Checkpoint

    # generation params
    sampling_params: SamplingParams = SamplingParams()


@json_schema_type
class EvaluateTextGenerationRequest(EvaluateTaskRequestCommon):
    """Request to evaluate text generation."""

    metrics: List[TextGenerationMetric]


@json_schema_type
class EvaluateQuestionAnsweringRequest(EvaluateTaskRequestCommon):
    """Request to evaluate question answering."""

    metrics: List[QuestionAnsweringMetric]


@json_schema_type
class EvaluateSummarizationRequest(EvaluateTaskRequestCommon):
    """Request to evaluate summarization."""

    metrics: List[SummarizationMetric]


class EvaluationJobStatusResponse(BaseModel):
    job_uuid: str


@json_schema_type
class EvaluationJobArtifactsResponse(BaseModel):
    """Artifacts of a evaluation job."""

    job_uuid: str


class Evaluations(Protocol):
    @webmethod(route="/evaluate/text_generation/")
    def post_evaluate_text_generation(
        self,
        request: EvaluateTextGenerationRequest,
    ) -> EvaluationJob: ...

    @webmethod(route="/evaluate/question_answering/")
    def post_evaluate_question_answering(
        self,
        request: EvaluateQuestionAnsweringRequest,
    ) -> EvaluationJob: ...

    @webmethod(route="/evaluate/summarization/")
    def post_evaluate_summarization(
        self,
        request: EvaluateSummarizationRequest,
    ) -> EvaluationJob: ...

    @webmethod(route="/evaluate/jobs")
    def get_evaluation_jobs(self) -> List[EvaluationJob]: ...

    @webmethod(route="/evaluate/job/status")
    def get_evaluation_job_status(
        self, job_uuid: str
    ) -> EvaluationJobStatusResponse: ...

    # sends SSE stream of logs
    @webmethod(route="/evaluate/job/logs")
    def get_evaluation_job_logstream(self, job_uuid: str) -> EvaluationJobLogStream: ...

    @webmethod(route="/evaluate/job/cancel")
    def cancel_evaluation_job(self, job_uuid: str) -> None: ...

    @webmethod(route="/evaluate/job/artifacts")
    def get_evaluation_job_artifacts(
        self, job_uuid: str
    ) -> EvaluationJobArtifactsResponse: ...
