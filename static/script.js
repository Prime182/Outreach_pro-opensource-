document.addEventListener('DOMContentLoaded', function() {
    // Dark mode toggle functionality
    const themeToggle = document.getElementById('theme-toggle');
    
    // Check for saved theme preference or use system preference
    const savedTheme = localStorage.getItem('theme');
    const systemPrefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    
    // Function to apply theme
    function applyTheme(isDark) {
        if (isDark) {
            document.documentElement.classList.add('dark');
            if (themeToggle) themeToggle.checked = true;
        } else {
            document.documentElement.classList.remove('dark');
            if (themeToggle) themeToggle.checked = false;
        }
    }
    
    // Set initial state
    applyTheme(savedTheme === 'dark' || (!savedTheme && systemPrefersDark));
    
    // Toggle theme when switch is clicked
    if (themeToggle) {
        themeToggle.addEventListener('change', function() {
            applyTheme(this.checked);
            localStorage.setItem('theme', this.checked ? 'dark' : 'light');
        });
    }

    // Listen for system preference changes
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', e => {
        if (!localStorage.getItem('theme')) {
            applyTheme(e.matches);
        }
    });

    const searchForm = document.getElementById('searchForm');
    const resultsTable = document.getElementById('resultsTable');
    const selectedContacts = document.getElementById('selectedContacts');
    const selectAllCheckbox = document.getElementById('selectAll');
    const saveButton = document.getElementById('saveButton');
    
    let selectedContactsList = new Set();

    // Handle search form submission
    searchForm.addEventListener('submit', async function(e) {
        e.preventDefault();
        
        // Get form data
        const formData = new FormData();
        formData.append('domain', document.getElementById('domain').value);
        
        const name = document.getElementById('name').value;
        if (name) formData.append('name', name);
        
        const department = document.getElementById('department').value;
        if (department) formData.append('department', department);
        
        const seniority = document.getElementById('seniority').value;
        if (seniority) formData.append('seniority', seniority);
        
        try {
            const response = await fetch('/search', {
                method: 'POST',
                body: formData
            });
            
            const data = await response.json();
            displayResults(data.results);
            
            // Display domain info if available
            if (data.domain_info) {
                displayDomainInfo(data.domain_info);
            }
        } catch (error) {
            console.error('Error:', error);
            alert('An error occurred while searching. Please try again.');
        }
    });

    // Display search results
    function displayResults(results) {
        resultsTable.innerHTML = '';
        results.forEach((contact, index) => {
            const row = document.createElement('tr');
            row.innerHTML = `
                <td class="px-6 py-4 whitespace-nowrap">
                    <input type="checkbox" class="contact-checkbox rounded" data-index="${index}">
                </td>
                <td class="px-6 py-4 whitespace-nowrap text-gray-900">${contact.name || 'N/A'}</td>
                <td class="px-6 py-4 whitespace-nowrap text-gray-900">${contact.email || 'N/A'}</td>
                <td class="px-6 py-4 whitespace-nowrap text-gray-900">${contact.position || 'N/A'}</td>
                <td class="px-6 py-4 whitespace-nowrap text-gray-900">${contact.department || 'N/A'}</td>
                <td class="px-6 py-4 whitespace-nowrap">
                    ${contact.confidence ? 
                        `<span class="px-2 inline-flex text-xs leading-5 font-semibold rounded-full 
                            ${contact.confidence >= 80 ? 'bg-green-100 text-green-800' : 
                              contact.confidence >= 50 ? 'bg-yellow-100 text-yellow-800' : 
                              'bg-red-100 text-red-800'}">
                            ${contact.confidence}%
                        </span>` : 
                        'N/A'}
                </td>
            `;
            resultsTable.appendChild(row);
        });

        // Add event listeners to checkboxes
        document.querySelectorAll('.contact-checkbox').forEach(checkbox => {
            checkbox.addEventListener('change', function() {
                const index = this.dataset.index;
                const contact = results[index];
                
                if (this.checked) {
                    selectedContactsList.add(JSON.stringify(contact));
                } else {
                    selectedContactsList.delete(JSON.stringify(contact));
                }
                
                updateSelectedContactsList();
            });
        });
    }

    // Display domain information
    function displayDomainInfo(domainInfo) {
        const domainInfoContainer = document.getElementById('domainInfoContainer');
        if (!domainInfoContainer) {
            console.error('Domain info container #domainInfoContainer not found.');
            return;
        }
        // Clear previous domain info
        domainInfoContainer.innerHTML = ''; 

        const infoDiv = document.createElement('div');
        // infoDiv.className = 'domain-info-section bg-blue-50 border-l-4 border-blue-400 p-4 mb-4'; // Keep class if needed for other styling
        infoDiv.className = 'bg-blue-50 border-l-4 border-blue-400 p-4'; // Removed mb-4 as container will have it
        infoDiv.innerHTML = `
            <div class="flex">
                <div class="flex-shrink-0">
                    <svg class="h-5 w-5 text-blue-400" viewBox="0 0 20 20" fill="currentColor">
                        <path fill-rule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a1 1 0 000 2v3a1 1 0 001 1h1a1 1 0 100-2v-3a1 1 0 00-1-1H9z" clip-rule="evenodd"/>
                    </svg>
                </div>
                <div class="ml-3">
                    <h3 class="text-sm font-medium text-blue-800">Domain Information</h3>
                    <div class="mt-2 text-sm text-blue-700">
                        <p><strong>Organization:</strong> ${domainInfo.organization || 'N/A'}</p>
                        <p><strong>Email Pattern:</strong> ${domainInfo.pattern || 'N/A'}</p>
                        <p><strong>Disposable:</strong> ${domainInfo.disposable ? 'Yes' : 'No'}</p>
                        <p><strong>Webmail:</strong> ${domainInfo.webmail ? 'Yes' : 'No'}</p>
                    </div>
                </div>
            </div>
        `;
        
        domainInfoContainer.appendChild(infoDiv); // Append to the dedicated container
    }

    // Update selected contacts list
    function updateSelectedContactsList() {
        selectedContacts.innerHTML = '';
        selectedContactsList.forEach(contactStr => {
            const contact = JSON.parse(contactStr);
            const contactElement = document.createElement('div');
            contactElement.className = 'flex items-center justify-between p-2 bg-white rounded border border-gray-200';
            contactElement.innerHTML = `
                <div>
                    <p class="font-medium text-gray-900">${contact.name}</p>
                    <p class="text-sm text-gray-900">${contact.email}</p>
                    ${contact.position ? `<p class="text-sm text-gray-900">${contact.position}</p>` : ''}
                </div>
                <button class="remove-contact text-red-600 hover:text-red-900" data-contact='${contactStr}'>
                    Remove
                </button>
            `;
            selectedContacts.appendChild(contactElement);
        });

        // Add event listeners to remove buttons
        document.querySelectorAll('.remove-contact').forEach(button => {
            button.addEventListener('click', function() {
                const contactStr = this.dataset.contact;
                selectedContactsList.delete(contactStr);
                updateSelectedContactsList();
                
                // Uncheck the corresponding checkbox in the results table
                const checkboxes = document.querySelectorAll('.contact-checkbox');
                checkboxes.forEach(checkbox => {
                    const index = checkbox.dataset.index;
                    const contact = results[index];
                    if (JSON.stringify(contact) === contactStr) {
                        checkbox.checked = false;
                    }
                });
            });
        });
    }

    // Handle select all checkbox
    selectAllCheckbox.addEventListener('change', function() {
        const checkboxes = document.querySelectorAll('.contact-checkbox');
        checkboxes.forEach(checkbox => {
            checkbox.checked = this.checked;
            const index = checkbox.dataset.index;
            const contact = results[index];
            
            if (this.checked) {
                selectedContactsList.add(JSON.stringify(contact));
            } else {
                selectedContactsList.delete(JSON.stringify(contact));
            }
        });
        
        updateSelectedContactsList();
    });

    // Handle save button click
    saveButton.addEventListener('click', async function() {
        if (selectedContactsList.size === 0) {
            alert('Please select at least one contact to save.');
            return;
        }

        // userId is now handled by the server session, no need to get it from localStorage
        // or check for it here. The server will deny if not authenticated.

        try {
            // No need to add user_id here, server will get it from session
            const contactsToSave = Array.from(selectedContactsList).map(str => JSON.parse(str)); 
            
            const response = await fetch('/save-contacts', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify(contactsToSave) // Send contacts without user_id
            });

            if (!response.ok) {
                const errorResult = await response.json();
                alert(errorResult.detail || 'Failed to save contacts.');
                if (response.status === 401) { // Unauthorized
                    window.location.href = '/login'; // Redirect to login if session expired or invalid
                }
                return;
            }

            const result = await response.json();
            alert(result.message);
            
            // Clear selections after successful save
            selectedContactsList.clear();
            updateSelectedContactsList();
            selectAllCheckbox.checked = false;
            document.querySelectorAll('.contact-checkbox').forEach(checkbox => {
                checkbox.checked = false;
            });
        } catch (error) {
            console.error('Error:', error);
            alert('An error occurred while saving contacts. Please try again.');
        }
    });
});
