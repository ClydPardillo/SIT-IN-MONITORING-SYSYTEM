document.addEventListener('DOMContentLoaded', function() {
    // Get sidebar elements
    const sidebarToggle = document.getElementById('sidebar-toggle');
    const sidebar = document.querySelector('.sidebar');
    const sidebarOverlay = document.getElementById('sidebar-overlay');
    const sidebarInnerToggle = document.querySelectorAll('.sidebar-inner-toggle');
    
    // Check if sidebar collapse state is stored in localStorage
    const sidebarState = localStorage.getItem('sidebarCollapsed');
    if (sidebarState === 'true') {
        sidebar.classList.add('collapsed');
    }
    
    // Toggle sidebar when mobile button is clicked
    if (sidebarToggle) {
        sidebarToggle.addEventListener('click', function() {
            sidebar.classList.toggle('active');
            if (sidebar.classList.contains('active')) {
                sidebarOverlay.style.display = 'block';
            } else {
                sidebarOverlay.style.display = 'none';
            }
        });
    }
    
    // Toggle sidebar collapse when inner toggle is clicked
    if (sidebarInnerToggle) {
        sidebarInnerToggle.forEach(function(toggle) {
            toggle.addEventListener('click', function() {
                sidebar.classList.toggle('collapsed');
                
                // Store sidebar state in localStorage
                localStorage.setItem('sidebarCollapsed', sidebar.classList.contains('collapsed'));
            });
        });
    }
    
    // Close sidebar when overlay is clicked
    if (sidebarOverlay) {
        sidebarOverlay.addEventListener('click', function() {
            sidebar.classList.remove('active');
            sidebarOverlay.style.display = 'none';
        });
    }
    
    // Handle window resize
    window.addEventListener('resize', function() {
        if (window.innerWidth > 991) {
            sidebar.classList.remove('active');
            if (sidebarOverlay) {
                sidebarOverlay.style.display = 'none';
            }
        }
    });
}); 