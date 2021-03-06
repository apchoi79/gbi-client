$(document).ready(function() {
    var usernameContainer = $('#username').closest('.control-group');
    var passwordContainer = $('#password').closest('.control-group');
    var noAuthRequiredElement = $('.no-auth-required');
    var authRequired = function() {
        if(window.authServer.indexOf($('#url').val()) > -1) {
            usernameContainer.removeClass('hide');
            passwordContainer.removeClass('hide');
            noAuthRequiredElement.addClass('hide');
        } else {
            usernameContainer.addClass('hide');
            passwordContainer.addClass('hide');
            noAuthRequiredElement.removeClass('hide');
        }
    };
    if(window.authServer !== undefined) {
        $('#url').change(authRequired);
        authRequired();
    }

    $('#set-server-form').submit(function() {
        $('#load-context-document').show();
        return true;
    });
    $('#add-server-form').submit(function() {
        $('#load-context-document').show();
        return true;
    });
});
