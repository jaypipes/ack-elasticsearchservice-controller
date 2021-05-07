# Copyright Amazon.com Inc. or its affiliates. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License"). You may
# not use this file except in compliance with the License. A copy of the
# License is located at
#
#	 http://aws.amazon.com/apache2.0/
#
# or in the "license" file accompanying this file. This file is distributed
# on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either
# express or implied. See the License for the specific language governing
# permissions and limitations under the License.

"""Integration tests for the ElasticsearchService API ElasticsearchDomain
resource
"""

import boto3
import datetime
import pytest
import logging
import time
from typing import Dict

from acktest.k8s import resource as k8s

from e2e import service_marker, CRD_GROUP, CRD_VERSION, load_resource
from e2e.replacement_values import REPLACEMENT_VALUES

RESOURCE_PLURAL = 'elasticsearchdomains'

DELETE_WAIT_INTERVAL_SLEEP_SECONDS = 15
DELETE_WAIT_AFTER_SECONDS = 30
DELETE_TIMEOUT_SECONDS = 240

CREATE_WAIT_INTERVAL_SLEEP_SECONDS = 15
CREATE_TIMEOUT_SECONDS = 900


@pytest.fixture(scope="module")
def es_client():
    return boto3.client('es')


# TODO(jaypipes): Move to k8s common library
def get_resource_arn(self, resource: Dict):
    assert 'ackResourceMetadata' in resource['status'] and \
        'arn' in resource['status']['ackResourceMetadata']
    return resource['status']['ackResourceMetadata']['arn']


@service_marker
@pytest.mark.canary
class TestDomain:
    def test_create_delete_7_9(self, es_client):
        resource_name = "my-es-domain"

        replacements = REPLACEMENT_VALUES.copy()
        replacements["DOMAIN_NAME"] = resource_name

        resource_data = load_resource(
            "domain_es7.9",
            additional_replacements=replacements,
        )
        logging.error(resource_data)

        # Create the k8s resource
        ref = k8s.CustomResourceReference(
            CRD_GROUP, CRD_VERSION, RESOURCE_PLURAL,
            resource_name, namespace="default",
        )
        k8s.create_custom_resource(ref, resource_data)
        cr = k8s.wait_resource_consumed_by_controller(ref)

        assert cr is not None
        assert k8s.get_resource_exists(ref)

        logging.debug(cr)

        # Let's check that the domain appears in AES
        aws_res = es_client.describe_elasticsearch_domain(DomainName=resource_name)

        logging.debug(aws_res)

        now = datetime.datetime.now()
        timeout = now + datetime.timedelta(seconds=CREATE_TIMEOUT_SECONDS)

        # An ES Domain gets its `DomainStatus.Created` field set to `True`
        # almost immediately, however the `DomainStatus.Processing` field is
        # set to `True` while Elasticsearch is being installed onto the worker
        # node(s). If you attempt to delete an ES Domain that is both Created
        # and Processing == True, AES will set the `DomainStatus.Deleted` field
        # to True as well, so the `Created`, `Processing` and `Deleted` fields
        # will all be True. It typically takes upwards of 4-6 minutes for an ES
        # Domain to reach Created = True && Processing = False and then another
        # 2 minutes or so after calling DeleteElasticsearchDomain for the ES
        # Domain to no longer appear in DescribeElasticsearchDomain API call.
        while aws_res['DomainStatus']['Processing'] == True:
            if datetime.datetime.now() >= timeout:
                pytest.fail("Timed out waiting for ES Domain to get DomainStatus.Processing == False")
            time.sleep(CREATE_WAIT_INTERVAL_SLEEP_SECONDS)

            aws_res = es_client.describe_elasticsearch_domain(DomainName=resource_name)

        logging.info(f"ES Domain {resource_name} creation succeeded and DomainStatus.Processing is now False")

        # Delete the k8s resource on teardown of the module
        k8s.delete_custom_resource(ref)

        logging.info(f"Deleted CR for ES Domain {resource_name}. Waiting {DELETE_WAIT_AFTER_SECONDS} before checking existence in AWS API")
        time.sleep(DELETE_WAIT_AFTER_SECONDS)

        now = datetime.datetime.now()
        timeout = now + datetime.timedelta(seconds=DELETE_TIMEOUT_SECONDS)

        # Domain should no longer appear in AES
        while True:
            if datetime.datetime.now() >= timeout:
                pytest.fail("Timed out waiting for ES Domain to being deleted in AES API")
            time.sleep(DELETE_WAIT_INTERVAL_SLEEP_SECONDS)

            try:
                aws_res = es_client.describe_elasticsearch_domain(DomainName=resource_name)
                if aws_res['DomainStatus']['Deleted'] == False:
                    pytest.fail("DomainStatus.Deleted is False for ES Domain that was deleted.")
            except es_client.exceptions.ResourceNotFoundException:
                break