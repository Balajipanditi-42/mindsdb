import os
import importlib
from pathlib import Path
import tempfile
import multipart

from flask import request, send_file, abort, current_app as ca
from flask_restx import Resource

from mindsdb_sql.parser.ast import Identifier
from mindsdb_sql.parser.dialects.mindsdb import CreateMLEngine

from mindsdb.api.http.utils import http_error
from mindsdb.api.http.namespaces.configs.handlers import ns_conf
from mindsdb.integrations.utilities.install import install_dependencies

from mindsdb.api.executor.controllers.session_controller import SessionController
from mindsdb.api.executor.command_executor import ExecuteCommands


@ns_conf.route('/')
class HandlersList(Resource):
    @ns_conf.doc('handlers_list')
    def get(self):
        '''List all db handlers'''
        handlers = ca.integration_controller.get_handlers_import_status()
        result = []
        for handler_type, handler_meta in handlers.items():
            row = {'name': handler_type}
            row.update(handler_meta)
            result.append(row)
        return result


@ns_conf.route('/<handler_name>/icon')
class HandlerIcon(Resource):
    @ns_conf.param('handler_name', 'Handler name')
    def get(self, handler_name):
        try:
            handlers_import_status = ca.integration_controller.get_handlers_import_status()
            icon_name = handlers_import_status[handler_name]['icon']['name']
            handler_folder = handlers_import_status[handler_name]['import']['folder']
            mindsdb_path = Path(importlib.util.find_spec('mindsdb').origin).parent
            icon_path = mindsdb_path.joinpath('integrations/handlers').joinpath(handler_folder).joinpath(icon_name)
            if icon_path.is_absolute() is False:
                icon_path = Path(os.getcwd()).joinpath(icon_path)
        except Exception:
            return abort(404)
        else:
            return send_file(icon_path)


@ns_conf.route('/<handler_name>/install')
class InstallDependencies(Resource):
    @ns_conf.param('handler_name', 'Handler name')
    def post(self, handler_name):
        handler_import_status = ca.integration_controller.get_handlers_import_status()
        if handler_name not in handler_import_status:
            return f'Unkown handler: {handler_name}', 400

        if handler_import_status[handler_name].get('import', {}).get('success', False) is True:
            return 'Installed', 200

        handler_meta = handler_import_status[handler_name]

        dependencies = handler_meta['import']['dependencies']
        if len(dependencies) == 0:
            return 'Installed', 200

        result = install_dependencies(dependencies)

        # reload it if any result, so we can get new error message
        ca.integration_controller.reload_handler_module(handler_name)
        if result.get('success') is True:
            return '', 200
        return http_error(
            500,
            'Failed to install dependency',
            result.get('error_message', 'unknown error')
        )


@ns_conf.route('/byom/<name>')
@ns_conf.param('name', "Name of the model")
class BYOMUpload(Resource):
    @ns_conf.doc('post_file')
    def post(self, name):
        params = {}

        def on_field(field):
            name = field.field_name.decode()
            value = field.value.decode()
            params[name] = value

        def on_file(file):
            params[file.field_name.decode()] = file.file_object

        temp_dir_path = tempfile.mkdtemp(prefix='mindsdb_file_')

        parser = multipart.create_form_parser(
            headers=request.headers,
            on_field=on_field,
            on_file=on_file,
            config={
                'UPLOAD_DIR': temp_dir_path.encode(),  # bytes required
                'UPLOAD_KEEP_FILENAME': True,
                'UPLOAD_KEEP_EXTENSIONS': True,
                'MAX_MEMORY_FILE_SIZE': 0
            }
        )

        while True:
            chunk = request.stream.read(8192)
            if not chunk:
                break
            parser.write(chunk)
        parser.finalize()
        parser.close()

        connection_args = {
            'code': params['code'].name.decode(),
            'modules': params['modules'].name.decode(),
            'type': params.get('type')
        }

        session = SessionController()

        base_ml_handler = session.integration_controller.get_ml_handler(name)
        byom_handler = base_ml_handler.get_ml_handler()   # !!!!
        byom_handler.update_engine(connection_args)

        engine_versions = [
            int(x) for x in byom_handler.engine_storage.get_connection_args()['versions'].keys()
        ]

        return {
            'last_engine_version': max(engine_versions),
            'engine_versions': engine_versions
        }

    @ns_conf.doc('put_file')
    def put(self, name):
        ''' upload new model
            params in FormData:
                - code
                - modules
        '''

        params = {}

        def on_field(field):
            name = field.field_name.decode()
            value = field.value.decode()
            params[name] = value

        def on_file(file):
            params[file.field_name.decode()] = file.file_object

        temp_dir_path = tempfile.mkdtemp(prefix='mindsdb_file_')

        parser = multipart.create_form_parser(
            headers=request.headers,
            on_field=on_field,
            on_file=on_file,
            config={
                'UPLOAD_DIR': temp_dir_path.encode(),  # bytes required
                'UPLOAD_KEEP_FILENAME': True,
                'UPLOAD_KEEP_EXTENSIONS': True,
                'MAX_MEMORY_FILE_SIZE': 0
            }
        )

        while True:
            chunk = request.stream.read(8192)
            if not chunk:
                break
            parser.write(chunk)
        parser.finalize()
        parser.close()

        params['code'].close()
        params['modules'].close()

        sql_session = SessionController()

        command_executor = ExecuteCommands(sql_session)

        connection_args = {
            'code': params['code'].name.decode(),
            'modules': params['modules'].name.decode(),
            'type': params.get('type')
        }

        ast_query = CreateMLEngine(
            name=Identifier(name),
            handler='byom',
            params=connection_args
        )
        command_executor.execute_command(ast_query)

        return '', 200
