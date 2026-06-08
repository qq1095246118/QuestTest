pipeline {
    agent any
    
    parameters {
        choice(name: 'ENV', choices: ['test', 'prod'], description: 'Target environment to run tests against')
        string(name: 'PYTHON_BIN', defaultValue: '/Users/wrh/.pyenv/versions/3.12.0/bin/python3.12', description: 'Python 3.12 executable used to install dependencies and run tests')
    }
    
    stages {
        stage('Checkout') {
            steps {
                checkout scm
            }
        }
        
        stage('Setup Environment & Install Dependencies') {
            steps {
                sh '''
                    ${PYTHON_BIN} --version
                    ${PYTHON_BIN} -m pip install -r requirements.txt
                '''
            }
        }
        
        stage('Run Automated Tests') {
            steps {
                sh '''
                    ${PYTHON_BIN} -m pytest --env=${params.ENV}
                '''
            }
        }
        
        stage('Generate Allure Report') {
            steps {
                allure([
                    includeProperties: false,
                    jdk: '',
                    properties: [],
                    reportBuildPolicy: 'ALWAYS',
                    results: [[path: 'allure-results']]
                ])
            }
        }
    }
    
}
