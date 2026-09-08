targetScope = 'subscription'

@minLength(1)
@maxLength(64)
@description('Name of the azd environment.')
param environmentName string

@description('Location for the Function App resources.')
@allowed([
  'centralus'
  'eastus'
  'eastus2'
  'northcentralus'
  'southcentralus'
  'westcentralus'
  'westus'
  'westus2'
])
@metadata({
  azd: {
    type: 'location'
  }
})
param location string = 'westus2'

@description('Subscription containing the sanctioned ACA Sandbox Group.')
param sandboxSubscriptionId string

@description('Resource group containing the sanctioned ACA Sandbox Group.')
param sandboxResourceGroupName string

@description('Existing sanctioned ACA Sandbox Group name.')
param sandboxGroupName string

@description('Region of the sanctioned ACA Sandbox Group.')
param sandboxRegion string = 'westus2'

@description('Reusable disk image name created in the sanctioned Sandbox Group.')
param sandboxDiskName string = 'stock-analysis-python'

@description('Container image converted into the reusable Sandbox Group disk.')
param sandboxBaseImage string = 'mcr.microsoft.com/devcontainers/python:1-3.13-bookworm'

@description('APIM AI Gateway endpoint that fronts the Azure OpenAI-compatible API.')
param apimAiGatewayEndpoint string

@description('Model/deployment name exposed through the APIM AI Gateway.')
param apimAiModel string

@description('Azure OpenAI API version accepted by the APIM AI Gateway.')
param apimAiApiVersion string = '2025-04-01-preview'

@description('Entra token scope required by the APIM AI Gateway.')
param apimAiTokenScope string = 'https://cognitiveservices.azure.com/.default'

@secure()
@description('Optional APIM subscription key. Prefer managed identity and leave this empty.')
param apimSubscriptionKey string = ''

@description('Outlook recipient for completed reports.')
param toEmail string

@description('Timer schedule in six-field NCRONTAB format. Default is weekdays at 13:30 UTC.')
param stockAnalysisSchedule string = '0 30 13 * * 1-5'

@description('Default comma-separated stock symbols for timer runs.')
param defaultStockSymbols string = 'MSFT,NVDA,AAPL'

@description('Default objective for timer runs.')
param defaultAnalysisObjective string = 'Produce an evidence-based comparative investment research report.'

@description('Connector Namespace location.')
param connectorGatewayLocation string = 'westcentralus'

var abbrs = loadJsonContent('./abbreviations.json')
var resourceToken = toLower(uniqueString(subscription().id, environmentName, location))
var tags = {
  'azd-env-name': environmentName
  workload: 'stock-analysis-agent'
}
var functionAppName = '${abbrs.webSitesFunctions}stock-${resourceToken}'
var deploymentStorageContainerName = 'app-package-${take(functionAppName, 32)}-${take(toLower(uniqueString(functionAppName, resourceToken)), 7)}'
var deployerPrincipalId = deployer().objectId
var connectorGatewayName = 'cg-stock-${resourceToken}'
var office365ConnectionName = 'office365-outlook'
var office365McpServerConfigName = 'o365-outlook-send-email-only'

resource rg 'Microsoft.Resources/resourceGroups@2021-04-01' = {
  name: '${abbrs.resourcesResourceGroups}${environmentName}'
  location: location
  tags: tags
}

module apiUserAssignedIdentity 'br/public:avm/res/managed-identity/user-assigned-identity:0.4.1' = {
  name: 'apiUserAssignedIdentity'
  scope: rg
  params: {
    location: location
    tags: tags
    name: '${abbrs.managedIdentityUserAssignedIdentities}stock-${resourceToken}'
  }
}

module sandboxRbac './app/sandbox-rbac.bicep' = {
  name: 'sandboxRbac'
  scope: resourceGroup(sandboxSubscriptionId, sandboxResourceGroupName)
  params: {
    sandboxGroupName: sandboxGroupName
    functionPrincipalId: apiUserAssignedIdentity.outputs.principalId
    deployerPrincipalId: deployerPrincipalId
  }
}

module office365Connector './app/connector-gateway.bicep' = {
  name: 'office365Connector'
  scope: rg
  params: {
    connectorGatewayName: connectorGatewayName
    connectionName: office365ConnectionName
    mcpServerConfigName: office365McpServerConfigName
    location: connectorGatewayLocation
    tags: tags
    managedIdentityPrincipalId: apiUserAssignedIdentity.outputs.principalId
    deployerPrincipalId: deployerPrincipalId
    tenantId: tenant().tenantId
  }
}

module appServicePlan 'br/public:avm/res/web/serverfarm:0.1.1' = {
  name: 'appserviceplan'
  scope: rg
  params: {
    name: '${abbrs.webServerFarms}${resourceToken}'
    sku: {
      name: 'FC1'
      tier: 'FlexConsumption'
    }
    reserved: true
    location: location
    tags: tags
  }
}

module api './app/api.bicep' = {
  name: 'api'
  scope: rg
  dependsOn: [
    sandboxRbac
  ]
  params: {
    name: functionAppName
    location: location
    tags: tags
    applicationInsightsName: monitoring.outputs.name
    appServicePlanId: appServicePlan.outputs.resourceId
    runtimeName: 'python'
    runtimeVersion: '3.13'
    storageAccountName: storage.outputs.name
    deploymentStorageContainerName: deploymentStorageContainerName
    identityId: apiUserAssignedIdentity.outputs.resourceId
    identityClientId: apiUserAssignedIdentity.outputs.clientId
    instanceMemoryMB: 4096
    maximumInstanceCount: 10
    appSettings: {
      AZURE_CLIENT_ID: apiUserAssignedIdentity.outputs.clientId
      APIM_AI_GATEWAY_ENDPOINT: apimAiGatewayEndpoint
      APIM_AI_MODEL: apimAiModel
      APIM_AI_API_VERSION: apimAiApiVersion
      APIM_AI_TOKEN_SCOPE: apimAiTokenScope
      APIM_SUBSCRIPTION_KEY: apimSubscriptionKey
      ACA_SANDBOX_SUBSCRIPTION_ID: sandboxSubscriptionId
      ACA_SANDBOX_RESOURCE_GROUP: sandboxResourceGroupName
      ACA_SANDBOX_GROUP: sandboxGroupName
      ACA_SANDBOX_REGION: sandboxRegion
      ACA_SANDBOX_DISK: sandboxDiskName
      ACA_SANDBOX_CPU: '2000m'
      ACA_SANDBOX_MEMORY: '4096Mi'
      ACA_SANDBOX_TIMEOUT_SECONDS: '600'
      ACA_SANDBOX_EGRESS_HOSTS: 'pypi.org,files.pythonhosted.org,query1.finance.yahoo.com,query2.finance.yahoo.com,fc.yahoo.com,data.sec.gov,www.sec.gov,login.microsoftonline.com,*.apihub.azure.com'
      TO_EMAIL: toEmail
      O365_MCP_SERVER_URL: office365Connector.outputs.mcpEndpointUrl
      O365_MCP_SCOPE: 'https://apihub.azure.com/.default'
      SEC_USER_AGENT: 'stock-analysis-agent ${toEmail}'
      STOCK_ANALYSIS_SCHEDULE: stockAnalysisSchedule
      DEFAULT_STOCK_SYMBOLS: defaultStockSymbols
      DEFAULT_ANALYSIS_OBJECTIVE: defaultAnalysisObjective
      DEFAULT_MARKET_PERIOD: '1y'
      DEFAULT_MARKET_INTERVAL: '1d'
      ENABLE_MULTIPLATFORM_BUILD: 'true'
    }
  }
}

module storage 'br/public:avm/res/storage/storage-account:0.8.3' = {
  name: 'storage'
  scope: rg
  params: {
    name: '${abbrs.storageStorageAccounts}${resourceToken}'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: true
    dnsEndpointType: 'Standard'
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      defaultAction: 'Allow'
      bypass: 'AzureServices'
    }
    blobServices: {
      containers: [{ name: deploymentStorageContainerName }]
    }
    minimumTlsVersion: 'TLS1_2'
    location: location
    tags: tags
  }
}

module rbac './app/rbac.bicep' = {
  name: 'rbacAssignments'
  scope: rg
  params: {
    storageAccountName: storage.outputs.name
    appInsightsName: monitoring.outputs.name
    managedIdentityPrincipalId: apiUserAssignedIdentity.outputs.principalId
    deployerPrincipalId: deployerPrincipalId
  }
}

module logAnalytics 'br/public:avm/res/operational-insights/workspace:0.7.0' = {
  name: '${uniqueString(deployment().name, location)}-loganalytics'
  scope: rg
  params: {
    name: '${abbrs.operationalInsightsWorkspaces}${resourceToken}'
    location: location
    tags: tags
    dataRetention: 30
  }
}

module monitoring 'br/public:avm/res/insights/component:0.4.1' = {
  name: '${uniqueString(deployment().name, location)}-appinsights'
  scope: rg
  params: {
    name: '${abbrs.insightsComponents}${resourceToken}'
    location: location
    tags: tags
    workspaceResourceId: logAnalytics.outputs.resourceId
    disableLocalAuth: true
  }
}

output AZURE_LOCATION string = location
output AZURE_FUNCTION_NAME string = api.outputs.SERVICE_API_NAME
output ACA_SANDBOX_SUBSCRIPTION_ID string = sandboxSubscriptionId
output ACA_SANDBOX_RESOURCE_GROUP string = sandboxResourceGroupName
output ACA_SANDBOX_GROUP string = sandboxGroupName
output ACA_SANDBOX_REGION string = sandboxRegion
output ACA_SANDBOX_DISK string = sandboxDiskName
output ACA_SANDBOX_BASE_IMAGE string = sandboxBaseImage
output TO_EMAIL string = toEmail
output O365_CONNECTOR_GATEWAY_NAME string = office365Connector.outputs.connectorGatewayName
output O365_CONNECTION_ID string = office365Connector.outputs.connectionId
output O365_MCP_SERVER_URL string = office365Connector.outputs.mcpEndpointUrl
output STOCK_ANALYSIS_HTTP_URL string = 'https://${api.outputs.SERVICE_API_NAME}.azurewebsites.net/stock-analysis'
